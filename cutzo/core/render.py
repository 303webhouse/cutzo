"""Turning an episode into finished audio.

A render is a list of pieces placed on the finished timeline. The conversation
pieces come from voice.f32 (the parts you kept, joined with short crossfades);
show-kit pieces (music, stingers, sound bites) come from the kit folder. Pieces
are mixed into one stereo working file, then FFmpeg sets the loudness and encodes.
Shows with video (The New Heretics) also get a `Picture`: the layers of the frame over
time, which cutzo.core.video turns into the finished video.

Times: `start`/`end` are inside the piece's source, `at` is on the finished timeline.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..tools import ffmpeg
from . import audio, timeline

XFADE = 0.012  # seconds of crossfade at every cut in the conversation
PEAK_MARGIN = 2.0  # dB between the limiter and the true-peak limit (room for encoding overshoot)
MONO_IN_STEREO_DB = 10 * math.log10(2)  # a one-channel sound played in both channels measures 3 dB louder
MAX_GAIN_TRIES = 3  # measuring passes to find the gain that lands on the loudness target after the limiter
GAIN_CLOSE_ENOUGH = 0.1  # LU
# Music dips under voices ("ducking"): it starts going down DUCK_ATTACK seconds before a voice comes
# in, comes back up over DUCK_RELEASE seconds after it stops, and stays down across gaps shorter than
# DUCK_JOIN seconds (so it doesn't bob between sentences).
DUCK_ATTACK = 0.4
DUCK_RELEASE = 1.0
DUCK_JOIN = 1.0


@dataclass
class Piece:
    src: Path
    channels: int
    start: float
    end: float
    at: float
    gain_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    kind: str = "voice"  # voice, music, effect, bite
    label: str = ""
    # fades: "linear" (crossfades at cuts), "exp" (rises evenly in decibels from -50 dB) or "premiere"
    # (evenly from -30 dB, the shape of Nick's music fade-ins in Spooky Sode, measured Oct 15, 2026)
    curve: str = "linear"

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def until(self) -> float:
        return self.at + self.length

    def view(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "at": round(self.at, 3), "length": round(self.length, 3),
                "gain_db": round(self.gain_db, 1)}


@dataclass
class Layer:
    """One picture on the finished timeline. Layers stack in order: later ones cover earlier ones.

    kind "video": a kit video (`src` is its id) from `start` seconds into it, played at `speed`;
    "still": one frame of a kit video (the one at `start`), held; "title": the episode's title graphic.

    blend "normal" covers what's below (fades are dissolves); "screen" lightens it, like Premiere's
    Screen mode: black lets the picture below show through, so a flame video on black adds only its
    flames (and its fades go to and from black, which is the same thing as fading its opacity)."""
    kind: str
    src: str
    at: float
    length: float
    start: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    label: str = ""
    blend: str = "normal"
    speed: float = 1.0

    @property
    def until(self) -> float:
        return self.at + self.length

    @property
    def reach(self) -> float:
        """How far into its kit video the layer goes (seconds of the video)."""
        return self.start + (self.length * self.speed if self.kind == "video" else 0.0)

    def view(self) -> dict[str, Any]:
        out = {"kind": self.kind, "src": self.src, "label": self.label, "at": round(self.at, 3),
               "length": round(self.length, 3)}
        if self.blend != "normal":
            out["blend"] = self.blend
        if self.speed != 1.0:
            out["speed"] = round(self.speed, 4)
        return out


@dataclass
class Picture:
    """What the video shows: the layers, when the spectrum shows ((start, end, fade in, fade out)
    windows) and how long the picture takes to fade to black at the very end."""
    layers: list[Layer] = field(default_factory=list)
    spectrum: list[tuple[float, float, float, float]] = field(default_factory=list)
    fade_to_black: float = 0.0


@dataclass
class Plan:
    pieces: list[Piece] = field(default_factory=list)
    chapters: list[dict[str, Any]] = field(default_factory=list)  # finished-file times
    talk_start: float = 0.0
    talk_end: float = 0.0
    duck_db: float = 0.0  # how far music dips while someone talks over it (0 = never)
    picture: Picture | None = None
    talk_reference_lufs: float = -19.2  # the talk loudness the kit levels were measured against

    @property
    def total(self) -> float:
        ends = [p.until for p in self.pieces]
        if self.picture:
            ends += [layer.until for layer in self.picture.layers]
        return max(ends, default=0.0)

    def voice_spans(self) -> list[tuple[float, float]]:
        """When someone is talking on the finished timeline (gaps under DUCK_JOIN joined)."""
        spans: list[list[float]] = []
        for a, b in sorted((p.at, p.until) for p in self.pieces if p.kind == "voice" and p.length > 0):
            if spans and a <= spans[-1][1] + DUCK_JOIN:
                spans[-1][1] = max(spans[-1][1], b)
            else:
                spans.append([a, b])
        return [(a, b) for a, b in spans]


def duck_curve(t: np.ndarray, spans: list[tuple[float, float]], depth_db: float) -> np.ndarray:
    """Gain (0..1) for music at times `t` (seconds): down by `depth_db` while a voice plays, with
    straight ramps in decibels before and after each voice span."""
    if depth_db <= 0 or not spans or not len(t):
        return np.ones(len(t), dtype=np.float32)
    db = np.zeros(len(t), dtype=np.float64)
    lo_t, hi_t = float(t[0]), float(t[-1])
    first = bisect.bisect_left([b for _, b in spans], lo_t - DUCK_RELEASE)  # spans are sorted and apart
    for a, b in spans[first:]:
        lo, hi = a - DUCK_ATTACK, b + DUCK_RELEASE
        if lo > hi_t:
            break
        if hi < lo_t:
            continue
        i, j = np.searchsorted(t, lo), np.searchsorted(t, hi)
        if j <= i:
            continue
        x = t[i:j]
        ramp = np.minimum(np.clip((x - lo) / DUCK_ATTACK, 0.0, 1.0), np.clip((hi - x) / DUCK_RELEASE, 0.0, 1.0))
        db[i:j] = np.minimum(db[i:j], -depth_db * ramp)
    return (10.0 ** (db / 20.0)).astype(np.float32)


def conversation(voice_path: Path, kept: list[tuple[float, float]], at: float,
                 breaks: dict[float, float] | None = None) -> tuple[list[Piece], Callable[[float], float], float]:
    """Place the kept stretches of the conversation one after another from `at`.

    `breaks` maps a recording time (a chapter start) to seconds of pause inserted just before it
    (for segment breaks). Returns the pieces, a function mapping recording time -> finished time,
    and where the talk ends."""
    pending = sorted((breaks or {}).items())
    pieces: list[Piece] = []
    spans: list[tuple[float, float, float]] = []  # (recording a, recording b, finished time of a)
    cursor = at
    for a, b in kept:
        # A break can fall inside a kept stretch: split the stretch there.
        edges = [a, *[t for t, _ in pending if a < t < b], b]
        for x, y in zip(edges, edges[1:]):
            gap = 0.0
            while pending and pending[0][0] <= x + 1e-6:
                gap += pending.pop(0)[1]
            overlap = XFADE if pieces and gap == 0.0 else 0.0
            cursor += gap
            start_at = cursor - overlap
            pieces.append(Piece(src=voice_path, channels=1, start=x, end=y, at=start_at,
                                fade_in=XFADE if pieces else 0.0, fade_out=XFADE, kind="voice", label="Conversation"))
            spans.append((x, y, start_at))
            cursor = start_at + (y - x)
    talk_end = cursor

    def to_finished(t: float) -> float:
        for x, y, f in spans:
            if x <= t < y:
                return f + (t - x)
        for x, _, f in spans:  # inside a cut: where talking resumes
            if x > t:
                return f
        return talk_end

    return pieces, to_finished, talk_end


def mix(plan: Plan, dst: Path, *, check: Callable[[], None] | None = None,
        progress: Callable[[float], None] | None = None) -> Path:
    """Mix every piece into one stereo working file (interleaved .f32, 48 kHz)."""
    rate = audio.RATE
    frames = int(np.ceil(plan.total * rate)) + 1
    out = np.memmap(dst, dtype="<f4", mode="w+", shape=(frames, 2))
    block = audio.BLOCK
    sources: dict[tuple[Path, int], np.ndarray] = {}
    spans = plan.voice_spans() if plan.duck_db > 0 else []
    for pos in range(0, frames, block):
        if check:
            check()
        end = min(frames, pos + block)
        acc = np.zeros((end - pos, 2), dtype=np.float32)
        t0, t1 = pos / rate, end / rate
        for p in plan.pieces:
            if p.until <= t0 or p.at >= t1 or p.length <= 0:
                continue
            key = (p.src, p.channels)
            if key not in sources:
                sources[key] = audio.Pcm(p.src, channels=p.channels).open()
            data = sources[key]
            # Output frames this piece covers inside the block.
            a = max(pos, int(round(p.at * rate)))
            b = min(end, int(round(p.until * rate)))
            if b <= a:
                continue
            src_a = int(round(p.start * rate)) + (a - int(round(p.at * rate)))
            src_b = src_a + (b - a)
            if src_a >= len(data):
                continue
            src_b = min(src_b, len(data))
            seg = np.asarray(data[src_a:src_b], dtype=np.float32)
            n = len(seg)
            if n == 0:
                continue
            gain = np.full(n, 10 ** (p.gain_db / 20.0), dtype=np.float32)
            local = (np.arange(a, a + n) / rate) - p.at  # seconds into the piece
            if p.fade_in > 0:
                gain *= _shape(np.clip(local / p.fade_in, 0.0, 1.0), p.curve)
            if p.fade_out > 0:
                gain *= _shape(np.clip((p.length - local) / p.fade_out, 0.0, 1.0), p.curve)
            if spans and p.kind == "music":
                gain *= duck_curve(local + p.at, spans, plan.duck_db)
            if p.channels == 1:
                seg = np.repeat(seg, 2, axis=1) if seg.ndim == 2 else np.stack([seg, seg], axis=1)
            acc[a - pos:a - pos + n] += seg[:, :2] * gain[:, None]
        out[pos:end] = acc
        if progress:
            progress(end / frames)
    out.flush()
    del out
    return dst


def _shape(ramp: np.ndarray, curve: str) -> np.ndarray:
    """0..1 ramp -> gain. "exp" rises evenly in decibels (-50 dB to 0), which sounds smooth for music;
    "premiere" rises evenly from -30 dB (starting from silence over the first tenth, so there's no click)."""
    if curve == "exp":
        return np.where(ramp <= 0.0, 0.0, 10.0 ** ((ramp - 1.0) * 2.5)).astype(np.float32)
    if curve == "premiere":
        return (10.0 ** ((ramp - 1.0) * 1.5) * np.clip(ramp * 10.0, 0.0, 1.0)).astype(np.float32)
    return ramp.astype(np.float32)


def kept_ranges(duration: float, edits: list[dict[str, Any]]) -> list[tuple[float, float]]:
    cuts = timeline.merge((e["start"], e["end"]) for e in edits if e["type"] == "cut")
    return [(a, b) for a, b in timeline.kept(duration, cuts) if b - a > 0.02]


def encode(mix_path: Path, dst: Path, spec: dict[str, Any], *, tags: dict[str, str], chapters: list[dict[str, Any]],
           total: float, check: Callable[[], None] | None = None, measured: dict[str, float] | None = None,
           gain_db: float | None = None, ceiling_db: float | None = None,
           progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    """Set the loudness (measure, one steady gain, peak limiter) and encode the stereo mix into the
    finished file. `gain_db` sets the gain outright (a second try, when the first came out a little
    off target); `ceiling_db` (dBFS) lowers the limiter when encoding pushed the peaks over the limit."""
    def report(share: float, label: str) -> None:
        if progress:
            progress(share, label)

    if measured is None:
        report(0.0, "Measuring loudness")
        measured = ffmpeg.loudness(mix_path, channels=2, duration=total, check=check,
                                   progress=lambda p: report(0.4 * p, "Measuring loudness"))
    meta = dst.with_name(dst.stem + ".ffmeta.txt")
    meta.write_text(ffmpeg.ffmetadata(tags, chapters, total), encoding="utf-8")
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    # One steady gain to reach the loudness target (no pumping), then a peak limiter that only
    # touches the loudest moments. MP3 and AAC encoding push peaks up again (by 1 to 2 dB on music
    # in tests), so the limiter sits PEAK_MARGIN under the limit; the check catches the rest.
    if ceiling_db is None:
        ceiling_db = spec["true_peak_max"] - PEAK_MARGIN

    def chain(g: float) -> str:
        return f"volume={g:.2f}dB,alimiter=limit={10 ** (ceiling_db / 20.0):.4f}:attack=5:release=60:level=false"

    if gain_db is not None:
        gain = gain_db
    else:
        # The limiter takes some loudness away (at YouTube's -14 LUFS enough for Spooky Sode's first
        # encode to miss), so measure what gain plus limiter make and correct the gain before encoding:
        # much quicker than encoding twice (AAC at 384 kbps runs at only about 9x real time). Encoding
        # itself barely moves the loudness; the check afterwards still catches anything left.
        gain = spec["loudness"] - measured["integrated"]
        for _ in range(MAX_GAIN_TRIES):
            report(0.4, "Setting the loudness")
            out = ffmpeg.loudness(mix_path, channels=2, duration=total, check=check, filters=chain(gain), peak=False)
            off = spec["loudness"] - out["integrated"]
            if abs(off) <= GAIN_CLOSE_ENOUGH:
                break
            gain += off
    filters = chain(gain)
    args = [*ffmpeg.raw_input_args(mix_path, 2), "-i", str(meta), "-map", "0:a", "-map_metadata", "1",
            "-map_chapters", "1", "-af", filters, "-ac", str(spec["channels"]), "-ar", str(spec["rate"])]
    if spec["codec"] == "mp3":
        args += ["-c:a", "libmp3lame", "-b:a", f"{spec['bitrate_kbps']}k", "-id3v2_version", "3", "-write_xing", "1"]
    elif spec["codec"] == "aac":
        args += ["-c:a", "aac", "-b:a", f"{spec['bitrate_kbps']}k", "-movflags", "+faststart"]
    args += ["-f", spec["format"], str(tmp)]
    try:
        ffmpeg.run(args, duration=total, check=check, progress=lambda p: report(0.4 + 0.6 * p, "Encoding"))
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
    return {"before": measured, "gain_db": round(gain, 2), "ceiling_db": round(ceiling_db, 2)}


def check_file(path: Path, spec: dict[str, Any], *, check: Callable[[], None] | None = None) -> dict[str, Any]:
    """Measure a finished file against its spec: ffprobe for the format, a loudness scan for the level."""
    info = ffmpeg.probe(path)
    a = info.get("audio") or {}
    lv = ffmpeg.loudness(path, duration=info.get("duration"), check=check)
    rules = []

    def rule(name: str, expected: str, found: str, ok: bool) -> None:
        rules.append({"rule": name, "expected": expected, "found": found, "pass": bool(ok)})

    codec_names = {"mp3": "MP3", "aac": "AAC"}
    rule("File type", codec_names.get(spec["codec"], spec["codec"]).upper(),
         codec_names.get(a.get("codec"), a.get("codec") or "no audio"), a.get("codec") == spec["codec"])
    rule("Channels", "Stereo" if spec["channels"] == 2 else "Mono",
         {1: "Mono", 2: "Stereo"}.get(a.get("channels"), str(a.get("channels"))), a.get("channels") == spec["channels"])
    rule("Sample rate", f"{spec['rate'] / 1000:g} kHz", f"{(a.get('rate') or 0) / 1000:g} kHz", a.get("rate") == spec["rate"])
    kbps = round((a.get("bit_rate") or info.get("bit_rate") or 0) / 1000)
    if spec["codec"] == "aac":  # AAC spends fewer bits on simple sound: the target is an upper bound
        rule("Bitrate", f"up to {spec['bitrate_kbps']} kbps", f"{kbps} kbps",
             spec["bitrate_kbps"] * 0.4 <= kbps <= spec["bitrate_kbps"] + 16)
    else:
        rule("Bitrate", f"{spec['bitrate_kbps']} kbps", f"{kbps} kbps", abs(kbps - spec["bitrate_kbps"]) <= 8)
    rule("Loudness", f"{spec['loudness']:g} LUFS (±{spec['loudness_tolerance']:g})", f"{lv['integrated']:.1f} LUFS",
         abs(lv["integrated"] - spec["loudness"]) <= spec["loudness_tolerance"])
    rule("Peaks", f"at or below {spec['true_peak_max']:g} dBTP", f"{lv['true_peak']:.1f} dBTP",
         lv["true_peak"] <= spec["true_peak_max"] + 0.05)
    return {"passed": all(r["pass"] for r in rules), "rules": rules, "duration": info.get("duration"),
            "bytes": path.stat().st_size, "loudness": lv}
