"""Building a real episode: line up the recordings, clean the voices, find dead air.

Everything happens in the episode's folder; the original recordings are only read.
Times are recording time: 0 is the start of the earliest recording.

Files in episodes/<id>/:
    work/src-<media>.f32     each recording, decoded (mono, 48 kHz); deleted after the build
    voice.f32                the cleaned conversation, every voice in place (what exports use)
    original.f32             the same, before cleanup (for listening side by side)
    preview.m4a              voice.f32 for the player
    original.m4a             original.f32 for the player
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .. import tools
from ..tools import ffmpeg, parakeet
from . import audio
from .errors import CutzoError
from .voice import VoiceSettings, clean

Report = Callable[[float, str], None]
Check = Callable[[], None]

DEAD_AIR = {"longer_than": 1.0, "keep": 0.5}
JOIN_GAP = 0.5  # seconds of quiet between combined recordings played one after another


@dataclass
class Source:
    media_id: str
    path: Path
    name: str
    person: str | None
    offset: float
    duration: float
    method: str
    confidence: float | None = None
    raw: Path | None = None
    cleaned: Path | None = None
    notes: list[str] = field(default_factory=list)


def choose_sources(media: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    """Which recordings carry the conversation. Returns (sources, combined mix for syncing or None, notes).

    One track per person beats a combined recording; a person's WAV beats the sound of their video;
    camera sound (GH5) is used only when there is nothing else."""
    def has_sound(m: dict[str, Any]) -> bool:
        if "probe" in m:
            return bool((m.get("probe") or {}).get("audio"))
        return m.get("kind") in ("audio", "video")

    usable = [m for m in media if m.get("exists", True) and has_sound(m)]
    tracks_audio = [m for m in usable if m.get("source") == "streamyard-track" and m.get("kind") == "audio"]
    tracks_video = [m for m in usable if m.get("source") == "streamyard-track" and m.get("kind") == "video"]
    mixes = [m for m in usable if m.get("source") in ("streamyard-mix", "mix")]
    others = [m for m in usable if m.get("source") == "other" and m.get("kind") == "audio"]
    camera = [m for m in usable if m.get("source") == "gh5" or (m.get("source") == "other" and m.get("kind") == "video")]
    notes: list[str] = []
    mix = mixes[0] if mixes else None
    if tracks_audio:
        people_with_wav = {m.get("person") for m in tracks_audio}
        extra = [m for m in tracks_video if m.get("person") not in people_with_wav]
        if extra:
            notes.append("Using the sound of the video for " + ", ".join(sorted({m['person'] for m in extra})) + ".")
        return tracks_audio + extra, mix, notes
    if tracks_video:
        notes.append("No separate sound files, so Cutzo uses the sound of each person's video.")
        return tracks_video, mix, notes
    if mixes:
        if len(mixes) > 1:  # the recording was stopped and started again: play them one after another
            mixes = join_order(mixes)
            notes.append(f"{len(mixes)} combined recordings, played one after another: "
                         + ", then ".join(m["name"] for m in mixes) + ".")
        return mixes, None, notes
    if others:
        return others, None, notes
    if camera:
        notes.append("Only camera sound was found; it's usually roomy. A microphone recording would sound better.")
        return camera[:1], None, notes
    raise CutzoError("None of the imported files has sound to work with.",
                     hint="Import the audio recordings (WAV or MP3) or the videos with sound.")


def is_mix(m: dict[str, Any]) -> bool:
    return m.get("source") in ("streamyard-mix", "mix")


def join_order(mixes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combined recordings in playing order: the order Nick set ("part"), else the order they were added."""
    return [m for _, m in sorted(enumerate(mixes), key=lambda km: (km[1].get("part") or 10**6, km[0]))]


def join(sources: list[Source], gap: float = JOIN_GAP) -> list[str]:
    """Combined recordings of one conversation (stopped and started again): each starts where the one
    before ends, with a short pause between."""
    at = 0.0
    for k, s in enumerate(sources):
        s.offset = round(at, 3)
        s.method = f"Part {k + 1} of {len(sources)}, played after the one before" if k else f"Part 1 of {len(sources)}"
        at += s.duration + gap
    return []


def decode_sources(chosen: list[dict[str, Any]], mix: dict[str, Any] | None, work: Path, *,
                   report: Report, check: Check) -> tuple[list[Source], Path | None]:
    """Decode each chosen recording (and the combined mix, used only for lining up) to a working file."""
    work.mkdir(parents=True, exist_ok=True)
    sources: list[Source] = []
    jobs = list(chosen) + ([mix] if mix else [])
    total = sum(float((m.get("probe") or {}).get("duration") or m.get("duration") or 60.0) for m in jobs) or 1.0
    done = 0.0
    mix_raw = None
    for m in jobs:
        length = float((m.get("probe") or {}).get("duration") or m.get("duration") or 0.0)
        dst = work / f"src-{m['id']}.f32"
        base = done

        def step(p: float, base=base, length=length, name=m["name"]) -> None:
            report((base + p * (length or 60.0)) / total, f"Reading {name}")

        ffmpeg.decode(Path(m["path"]), dst, channels=1, duration=length or None, progress=step, check=check)
        done += length or 60.0
        if m is mix:
            mix_raw = dst
            continue
        pcm = audio.Pcm(dst)
        sources.append(Source(
            media_id=m["id"], path=Path(m["path"]), name=m["name"], person=m.get("person"),
            offset=float(m.get("offset") or 0.0), duration=pcm.duration,
            method="From the file name" if m.get("offset") is not None else "Starts at 0:00",
            raw=dst))
    return sources, mix_raw


def line_up(sources: list[Source], mix_raw: Path | None, *, check: Check) -> list[str]:
    """Fine-tune each track's start against the combined recording, when there is one.
    StreamYard's file-name times are already close; this corrects small slips."""
    notes: list[str] = []
    if len(sources) == 1:
        sources[0].method = "Single recording, nothing to line up"
        sources[0].offset = 0.0
        return notes
    if mix_raw is None:
        return notes
    ref_env = audio.envelope(audio.Pcm(mix_raw))
    for s in sources:
        check()
        env = audio.envelope(audio.Pcm(s.raw))
        shift, confidence = audio.offset_by_sound(ref_env, env, max_shift=max(5.0, abs(s.offset) + 5.0))
        if confidence >= 0.2:
            if s.offset and abs(shift - s.offset) > 2.0:
                notes.append(f"{s.name}: its file name says {s.offset:.2f} s but the sound says {shift:.2f} s; "
                             "using the sound.")
            s.method = "Matched by sound" + (" (file name was close)" if s.offset and abs(shift - s.offset) <= 0.1 else "")
            s.offset = shift
            s.confidence = confidence
        else:
            s.method += " (couldn't confirm by sound)"
            s.confidence = confidence
    # Shift everything so the earliest recording starts at 0.
    earliest = min(s.offset for s in sources)
    for s in sources:
        s.offset = round(s.offset - earliest, 3)
    return notes


def place(tracks: list[tuple[Path, float]], dst: Path, total: float) -> Path:
    """Mix mono working files into one, each starting at its offset (seconds)."""
    rate = audio.RATE
    frames = int(round(total * rate))
    out = np.memmap(dst, dtype="<f4", mode="w+", shape=(max(frames, 1),))
    out[:] = 0.0
    for path, offset in tracks:
        data = audio.Pcm(path).open()[:, 0]
        start = int(round(offset * rate))
        for pos in range(0, len(data), audio.BLOCK):
            a = start + pos
            if a >= frames:
                break
            piece = np.asarray(data[pos:pos + audio.BLOCK], dtype=np.float32)[: frames - a]
            out[a:a + len(piece)] += piece
    peak = float(np.max(np.abs(out))) if frames else 0.0
    if peak > 0.99:
        out[:] *= 0.99 / peak
    out.flush()
    del out
    return dst


def preview(src: Path, dst: Path, *, check: Check, report: Callable[[float], None] | None = None) -> Path:
    """A small, seekable AAC copy of a working file for the browser player."""
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    ffmpeg.run([*ffmpeg.raw_input_args(src, 1), "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
                "-f", "mp4", str(tmp)], duration=audio.Pcm(src).duration, progress=report, check=check)
    tmp.replace(dst)
    return dst


def build_voice(ep_dir: Path, sources: list[Source], settings: VoiceSettings, *, report: Report,
                check: Check) -> tuple[Path, Path, float]:
    """Clean every source, place them in time, and write voice.f32 and original.f32 (+ previews)."""
    work = ep_dir / "work"
    total = max(s.offset + s.duration for s in sources)
    n = len(sources)
    for k, s in enumerate(sources):
        s.cleaned = work / f"voice-{s.media_id}.f32"
        label = f"Cleaning {s.person or s.name}" if n > 1 else "Cleaning the voices"
        clean(s.raw, s.cleaned, settings, work_dir=work, check=check,
              progress=lambda p, step, k=k, label=label: report((k + p) / n * 0.9, f"{label}: {step}"))
    voice_path, original_path = ep_dir / "voice.f32", ep_dir / "original.f32"
    if n == 1 and sources[0].offset == 0:
        shutil.move(str(sources[0].cleaned), str(voice_path))
        shutil.move(str(sources[0].raw), str(original_path))
    else:
        place([(s.cleaned, s.offset) for s in sources], voice_path, total)
        place([(s.raw, s.offset) for s in sources], original_path, total)
    report(0.92, "Making the listening copies")
    preview(voice_path, ep_dir / "preview.m4a", check=check)
    preview(original_path, ep_dir / "original.m4a", check=check)
    return voice_path, original_path, round(total, 3)


def find_dead_air(voice_path: Path, *, longer_than: float, keep: float, threshold_db: float | None = None
                  ) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Long silences to shorten. Returns cut suggestions and the levels used."""
    pcm = audio.Pcm(voice_path)
    env = audio.envelope(pcm)
    levels = {"speech_db": round(audio.speech_level(env), 1), "floor_db": round(audio.noise_floor(env), 1)}
    threshold = threshold_db if threshold_db is not None else audio.suggest_threshold(env)
    levels["threshold_db"] = threshold
    silences = audio.find_silences(env, threshold, min_len=longer_than)
    cuts = []
    for (a, b), (ca, cb) in zip(*_pairs(silences, longer_than, keep, pcm.duration)):
        cuts.append({"type": "cut", "start": ca, "end": cb, "reason": "dead air", "text": f"{b - a:.1f} s of silence"})
    return cuts, levels


def _pairs(silences: list[tuple[float, float]], longer_than: float, keep: float, duration: float):
    kept_silences, cuts = [], []
    for a, b in silences:
        made = audio.dead_air_cuts([(a, b)], longer_than=longer_than, keep=keep, duration=duration)
        if made:
            kept_silences.append((a, b))
            cuts.append(made[0])
    return kept_silences, cuts


def transcribe(voice_path: Path, work: Path, sources: list[Source] | None = None, *, report: Report,
               check: Check) -> dict[str, Any]:
    """Speech to text over the cleaned conversation (recording time). With separate tracks for
    several people, each word goes to whoever is loudest while it is said."""
    work.mkdir(parents=True, exist_ok=True)
    total = audio.Pcm(voice_path).duration
    wav = work / "speech-16k.wav"
    report(0.0, "Getting the audio ready")
    ffmpeg.run([*ffmpeg.raw_input_args(voice_path, 1), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav",
                str(wav)], duration=total, check=check, progress=lambda p: report(0.04 * p, "Getting the audio ready"))
    try:
        result = parakeet.transcribe(wav, work_dir=work, check=check,
                                     progress=lambda p: report(0.04 + 0.92 * p, "Writing down every word"))
    finally:
        wav.unlink(missing_ok=True)
    words = result["words"]
    report(0.96, "Matching words to the sound")
    env = audio.envelope(audio.Pcm(voice_path))
    anchor_words(words, env, audio.suggest_threshold(env))
    speakers: list[dict[str, str]] = []
    people = [s for s in (sources or []) if s.person and _track(s) is not None]
    if len(people) > 1:
        report(0.97, "Working out who said what")
        speakers = [{"id": f"S{k + 1}", "name": s.person or s.name} for k, s in enumerate(people)]
        attribute_speakers(words, people, [sp["id"] for sp in speakers])
    return {"engine": f"parakeet-cli (whisper.cpp {tools.WHISPER.version})", "model": f"parakeet-{tools.PARAKEET_MODEL.version}",
            "speakers": speakers, "words": words}


def anchor_words(words: list[dict[str, Any]], env: np.ndarray, quiet: float, reach: float = 0.6) -> None:
    """Give each word a moment that is surely part of it ("c", seconds): the middle of its sound,
    weighted by loudness; or, when its times fall wholly in a pause, the nearest sound within
    `reach` seconds. Whether a word counts as cut goes by this moment (transcript.anchor), so a
    word whose end speech to text stretched into the pause after it isn't struck through when
    that pause is shortened. `env` is the voice's 10 ms loudness outline, `quiet` its silence level."""
    hop, n = audio.HOP, len(env)
    loud = env >= quiet
    for w in words:
        a = max(0, int(float(w["s"]) / hop))
        b = min(n, max(a + 1, int(np.ceil(float(w["e"]) / hop))))
        if a >= n:
            continue
        on = loud[a:b]
        if on.any():
            power = 10 ** (env[a:b][on].astype(np.float64) / 10)
            t = (np.flatnonzero(on) + a + 0.5) * hop
            w["c"] = round(float((t * power).sum() / power.sum()), 3)
            continue
        lo, hi = max(0, a - int(reach / hop)), min(n, b + int(reach / hop))
        before, after = np.flatnonzero(loud[lo:a]), np.flatnonzero(loud[b:hi])
        near = []
        if len(before):
            near.append((a - (lo + int(before[-1])), lo + int(before[-1])))
        if len(after):
            near.append((int(after[0]) + 1, b + int(after[0])))
        if near:
            w["c"] = round((min(near)[1] + 0.5) * hop, 3)


def _track(s: Source) -> Path | None:
    for path in (s.cleaned, s.raw):
        if path is not None and Path(path).exists():
            return Path(path)
    return None


def attribute_speakers(words: list[dict[str, Any]], people: list[Source], ids: list[str]) -> None:
    """Mark each word with the speaker whose own track is loudest while it is said."""
    hop = audio.HOP
    outlines = []
    for s in people:
        env = audio.envelope(audio.Pcm(_track(s)))  # in the track's own time
        outlines.append((env, s.offset))
    for w in words:
        best, best_level = None, -1e9
        for (env, offset), sid in zip(outlines, ids):
            a = int((float(w["s"]) - offset) / hop)
            b = max(a + 1, int((float(w["e"]) - offset) / hop))
            if b <= 0 or a >= len(env):
                continue
            seg = env[max(0, a):min(len(env), b)]
            level = float(np.mean(10 ** (seg / 10.0))) if len(seg) else 0.0
            if level > best_level:
                best, best_level = sid, level
        if best:
            w["sp"] = best


def tidy(ep_dir: Path) -> None:
    """Remove the build's scratch files (the decoded recordings can be made again from the originals)."""
    shutil.rmtree(ep_dir / "work", ignore_errors=True)
