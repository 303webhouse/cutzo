"""Whine removal: steady tones in the background (a fan, a computer, electrical whine) that the noise
removal leaves in, because DeepFilterNet at the light setting treats a steady tone as part of the room.

Found in the pauses between words (from the transcript), where nobody talks: per 15-second window, the
median spectrum of the pauses, and any narrow peak at least `RISE_DB` above its neighbours is a tone.
Windows with tones are joined into stretches; each stretch gets a narrow notch (two band-rejects) at
every tone found in it, padded by `PAD` seconds and crossfaded in and out, so nothing else changes.

It works in place on voice.f32: the original samples of each stretch are kept in `<episode>/whine/` first,
so `restore` puts them back (switching it off, or before finding again). First used on Time to Sell
(Nov 5, 2026): a ~1,104 Hz whine with overtones at 961 and 2,650 Hz in the first minutes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from ..tools import ffmpeg
from . import audio

N = 8192  # samples per spectrum (5.9 Hz steps at 48 kHz)
WINDOW = 15.0  # seconds per window
MIN_FRAMES = 6  # pause frames a window needs to be judged
RISE_DB = 15.0  # how far a peak must stand above its neighbours to count as a tone
STRONG_DB = 20.0  # a tone found in only one window must stand out at least this far
QUIET_DB = 6.0  # pause frames within this of the quietest ones are judged (voices leak into the rest)
STEADY_DB = 6.0  # a tone must stand this far out of a single frame...
STEADY_SHARE = 0.7  # ...in this share of the quiet frames
LO, HI = 200.0, 12000.0  # Hz; below 200 a steady hum sits among voices' own pitches
NEIGHBOURS = 31  # spectrum steps either side used for "neighbours" (about 180 Hz)
MIN_GAP = 0.35  # seconds between words that count as a pause
PAD = 2.0  # seconds filtered before and after a stretch
FADE = 0.5  # seconds of crossfade in and out
SETTLE = 1.0  # extra seconds filtered and thrown away, so the filters have settled
MAX_UNKNOWN = 2  # windows without pauses allowed inside a stretch
JOIN_GAP = 60.0  # seconds: stretches this close with the same tone are one
MAX_TONES = 8

Check = Callable[[], None] | None


def pauses(words: Iterable[dict[str, Any]], min_gap: float = MIN_GAP) -> list[tuple[float, float]]:
    """Stretches between words, trimmed a little so word edges stay out."""
    ws = list(words)
    return [(a["e"] + 0.1, b["s"] - 0.06) for a, b in zip(ws, ws[1:]) if b["s"] - a["e"] >= min_gap]


def _window_tones(data: np.ndarray, gaps: list[tuple[float, float]], lo: float, hi: float,
                  rise_db: float) -> list[tuple[float, float]] | None:
    """(Hz, dB above neighbours) of the steady tones in these pauses, or None if there's too little pause.

    Only the quiet pause frames count (a laugh or someone talking softly where the transcript has no
    words would bring their voice's pitch in), a tone has to be there in most of them, and a window whose
    tones line up as one pitch's harmonics is a voice, not a whine."""
    rate = audio.RATE
    win = np.hanning(N)
    frames = []
    for g0, g1 in gaps:
        seg = np.asarray(data[int(g0 * rate):int(g1 * rate)], dtype=np.float64)
        for i in range(0, len(seg) - N + 1, N // 4):
            frames.append(np.abs(np.fft.rfft(seg[i:i + N] * win)) ** 2)
    if len(frames) < MIN_FRAMES:
        return None
    f = np.fft.rfftfreq(N, 1 / rate)
    band = (f >= 200) & (f <= 4000)
    # How loud each frame is, by the median of its spectrum: a voice raises it, a narrow tone doesn't.
    levels = 10 * np.log10(np.median(np.array(frames)[:, band], axis=1) + 1e-30)
    quiet = levels <= np.quantile(levels, 0.1) + QUIET_DB
    if quiet.sum() < MIN_FRAMES:
        return None
    each = 10 * np.log10(np.array(frames)[quiet] + 1e-20)
    p = np.median(each, axis=0)
    k = NEIGHBOURS
    pad = np.pad(p, k, mode="edge")
    base = np.median(np.lib.stride_tricks.sliding_window_view(pad, 2 * k + 1), axis=1)
    rise = p - base
    out = []
    for i in range(1, len(p) - 1):
        if not lo <= f[i] <= hi or rise[i] < rise_db or p[i] < p[i - 1] or p[i] < p[i + 1]:
            continue
        steady = np.mean(each[:, i - 1:i + 2].max(axis=1) - base[i] >= STEADY_DB)
        if steady < STEADY_SHARE:
            continue
        a, b, c = p[i - 1], p[i], p[i + 1]  # finer pitch from the peak's shape
        shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
        out.append((float(f[i] + shift * (f[1] - f[0])), float(rise[i])))
    if _harmonics(out):
        return []
    return out


def _harmonics(tones: list[tuple[float, float]]) -> bool:
    """True if these tones line up as one voice's pitch (70-400 Hz) and its overtones: three or more of them,
    or every one of two or more (all within the sixth overtone)."""
    hz = [t for t, _ in tones]
    if len(hz) < 2:
        return False
    for f0 in np.arange(70.0, 400.0, 0.5):
        fits = [x for x in hz if 1 <= round(x / f0) <= 6 and abs(x - round(x / f0) * f0) <= min(max(3.0, 0.01 * x), 0.08 * f0)]
        if len(fits) >= 3 or len(fits) == len(hz):
            return True
    return False


def _group(found: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """Tones from several windows, joined when within 2% of each other: centre, width to notch, strength."""
    tones: list[list[tuple[float, float]]] = []
    for hz, rise in sorted(found):
        if tones and hz <= tones[-1][-1][0] * 1.02:
            tones[-1].append((hz, rise))
        else:
            tones.append([(hz, rise)])
    out = []
    for t in tones:
        hz = [x for x, _ in t]
        spread = max(hz) - min(hz)
        out.append({"hz": round((max(hz) + min(hz)) / 2, 1), "width": round(max(30.0, spread + 20.0), 1),
                    "rise_db": round(max(r for _, r in t), 1)})
    out.sort(key=lambda t: -t["rise_db"])
    return sorted(out[:MAX_TONES], key=lambda t: t["hz"])


def find(voice_path: Path, words: Iterable[dict[str, Any]], *, start: float | None = None, end: float | None = None,
         lo: float = LO, hi: float = HI, rise_db: float = RISE_DB, check: Check = None) -> list[dict[str, Any]]:
    """Stretches of the recording with a steady whine: [{"start", "end", "tones": [{"hz", "width", "rise_db"}]}]."""
    pcm = audio.Pcm(voice_path)
    data = pcm.open()[:, 0]
    a = max(0.0, start or 0.0)
    b = min(pcm.duration, end if end is not None else pcm.duration)
    gaps = [g for g in pauses(words) if g[1] > a and g[0] < b]
    windows: list[tuple[float, float, list[tuple[float, float]] | None]] = []
    t = a
    while t < b:
        if check:
            check()
        w1 = min(b, t + WINDOW)
        inside = [(max(g0, t), min(g1, w1)) for g0, g1 in gaps if g1 > t and g0 < w1]
        windows.append((t, w1, _window_tones(data, inside, lo, hi, rise_db)))
        t = w1
    stretches: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    unknown = 0
    for w0, w1, tones in windows:
        if tones:
            if current is None:
                current = {"start": w0, "end": w1, "found": list(tones)}
            else:
                current["end"] = w1
                current["found"] += tones
            unknown = 0
        elif tones is None and current is not None and unknown < MAX_UNKNOWN:
            unknown += 1  # no pauses here to judge by: carry on if the whine is there after it
        else:
            if current:
                stretches.append(current)
            current, unknown = None, 0
    if current:
        stretches.append(current)
    joined: list[dict[str, Any]] = []
    for s in stretches:  # a whirr comes and goes: the same tone again soon after is the same whine
        last = joined[-1] if joined else None
        if last and s["start"] - last["end"] <= JOIN_GAP and any(
                abs(x - y) <= 0.02 * y for x, _ in s["found"] for y, _ in last["found"]):
            last["end"] = s["end"]
            last["found"] += s["found"]
        else:
            joined.append(s)
    stretches = [s for s in joined if s["end"] - s["start"] > WINDOW + 0.01 or max(r for _, r in s["found"]) >= STRONG_DB]
    return [{"start": round(s["start"], 2), "end": round(s["end"], 2), "tones": _group(s["found"])} for s in stretches]


def chain(tones: list[dict[str, Any]]) -> str:
    """FFmpeg filters: two narrow band-rejects per tone (deep enough for a tone that wanders a little)."""
    parts = []
    for t in tones:
        one = f"bandreject=f={t['hz']:.1f}:t=h:w={t['width']:.1f}"
        parts += [one, one]
    return ",".join(parts)


def _backup_name(a: int, b: int) -> str:
    return f"{a}-{b}.f32"


def remove(voice_path: Path, stretches: list[dict[str, Any]], backups: Path, *, check: Check = None) -> list[dict[str, Any]]:
    """Notch the tones out of each stretch of voice_path, in place, keeping the original samples in `backups`.
    Returns the stretches with the frames they changed."""
    rate = audio.RATE
    pcm = audio.Pcm(voice_path)
    total = pcm.frames
    backups.mkdir(parents=True, exist_ok=True)
    done = []
    for s in stretches:
        if check:
            check()
        if not s.get("tones"):
            continue
        a = max(0, int((s["start"] - PAD) * rate))
        b = min(total, int((s["end"] + PAD) * rate))
        a0 = max(0, a - int(SETTLE * rate))
        b0 = min(total, b + int(SETTLE * rate))
        data = np.memmap(voice_path, dtype="<f4", mode="r+")
        before = np.array(data[a:b], dtype=np.float32)
        backup = backups / _backup_name(a, b)
        if not backup.exists():
            before.astype("<f4").tofile(backup)
        raw = backups / f"{a}.raw.f32"
        out = backups / f"{a}.part.f32"
        try:
            np.asarray(data[a0:b0], dtype="<f4").tofile(raw)
            ffmpeg.filter_raw(raw, out, chain(s["tones"]), check=check)
            y = np.fromfile(out, dtype="<f4")
        finally:
            raw.unlink(missing_ok=True)
            out.unlink(missing_ok=True)
        y = y[a - a0:a - a0 + (b - a)]
        if len(y) < b - a:
            y = np.concatenate([y, before[len(y):]])
        fade = min(int(FADE * rate), (b - a) // 2)
        mix = np.ones(b - a, dtype=np.float32)
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        mix[:fade] = ramp
        mix[len(mix) - fade:] = ramp[::-1]
        data[a:b] = before * (1 - mix) + y.astype(np.float32) * mix
        data.flush()
        del data
        done.append({**s, "frames": [a, b]})
    return done


def restore(voice_path: Path, backups: Path) -> list[tuple[float, float]]:
    """Put back every stretch kept in `backups` (and delete the copies). Returns the times put back."""
    if not backups.exists():
        return []
    put_back = []
    total = audio.Pcm(voice_path).frames
    kept = []
    for f in backups.glob("*-*.f32"):
        try:
            a, b = (int(x) for x in f.stem.split("-"))
        except ValueError:
            continue
        kept.append((a, b, f))
    for a, b, f in sorted(kept, reverse=True):  # newest first, in case two ever overlapped
        samples = np.fromfile(f, dtype="<f4")
        if a < total and len(samples) == b - a and b <= total:
            data = np.memmap(voice_path, dtype="<f4", mode="r+")
            data[a:b] = samples
            data.flush()
            del data
            put_back.append((a / audio.RATE, b / audio.RATE))
        f.unlink()
    for f in backups.glob("*.f32"):  # half-finished pieces
        f.unlink()
    return put_back


def forget(backups: Path) -> None:
    """Drop the kept copies without putting them back (after a rebuild made a new voice.f32)."""
    if backups.exists():
        for f in backups.glob("*.f32"):
            f.unlink()


def describe(stretches: list[dict[str, Any]], fmt: Callable[[float], str]) -> str:
    parts = []
    for s in stretches:
        hz = ", ".join(f"{t['hz']:,.0f} Hz" for t in s["tones"])
        parts.append(f"{fmt(s['start'])}–{fmt(s['end'])} ({hz})")
    return "; ".join(parts)
