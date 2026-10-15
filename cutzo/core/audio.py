"""Audio maths on Cutzo's working files.

A working file is raw 32-bit float samples at 48 kHz with no header (".f32"),
written by FFmpeg (see cutzo.tools.ffmpeg). Cutzo memory-maps these files, so a
two-hour episode never has to fit in memory. Everything here is plain numpy.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

RATE = 48000
HOP = 0.01  # seconds per loudness reading
BLOCK = RATE * 30  # samples read at a time when streaming


@dataclass(frozen=True)
class Pcm:
    """A working file and how to read it."""

    path: Path
    channels: int = 1
    rate: int = RATE

    @property
    def frames(self) -> int:
        try:
            return self.path.stat().st_size // (4 * self.channels)
        except OSError:
            return 0

    @property
    def duration(self) -> float:
        return self.frames / self.rate

    def open(self) -> np.ndarray:
        """All samples as a (frames, channels) float32 array, memory-mapped (read-only)."""
        if self.frames == 0:
            return np.zeros((0, self.channels), dtype=np.float32)
        data = np.memmap(self.path, dtype="<f4", mode="r")
        return data[: self.frames * self.channels].reshape(-1, self.channels)

    def mono_blocks(self, start: float = 0.0, end: float | None = None, block: int = BLOCK) -> Iterator[tuple[int, np.ndarray]]:
        """(first frame, mono samples) pieces between two times."""
        data = self.open()
        a = max(0, int(round(start * self.rate)))
        b = len(data) if end is None else min(len(data), int(round(end * self.rate)))
        for pos in range(a, b, block):
            chunk = np.asarray(data[pos:min(b, pos + block)], dtype=np.float32)
            yield pos, chunk.mean(axis=1) if self.channels > 1 else chunk[:, 0]


def write_f32(path: Path, samples: np.ndarray) -> Path:
    np.asarray(samples, dtype="<f4").tofile(path)
    return path


# --------------------------------------------------------------- WAV reading
def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """A small WAV reader for 16/24/32-bit PCM and 32-bit float (stdlib `wave` can't read float).
    Returns (frames x channels float32, sample rate)."""
    raw = Path(path).read_bytes()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError(f"{path} isn't a WAV file")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(raw):
        cid, size = raw[pos:pos + 4], struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            data = body
            break
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise ValueError(f"{path} is missing its format or data")
    tag, channels, rate, _, _, bits = fmt
    if tag == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE: the real format is in the sub-format
        tag = 3 if bits == 32 and b"\x03\x00\x00\x00\x00\x00\x10\x00" in raw[12:200] else 1
    if tag == 3 and bits == 32:
        samples = np.frombuffer(data, dtype="<f4")
    elif tag == 1 and bits == 16:
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    elif tag == 1 and bits == 32:
        samples = np.frombuffer(data, dtype="<i4").astype(np.float32) / 2147483648.0
    elif tag == 1 and bits == 24:
        b = np.frombuffer(data[: len(data) // 3 * 3], dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        samples = ints.astype(np.float32) / 8388608.0
    else:
        raise ValueError(f"{path}: unsupported WAV format (tag {tag}, {bits} bits)")
    frames = len(samples) // channels
    return samples[: frames * channels].reshape(frames, channels).astype(np.float32, copy=False), rate


# ------------------------------------------------------------------ analysis
def db(x: np.ndarray | float, floor: float = -120.0) -> np.ndarray:
    return np.maximum(20.0 * np.log10(np.maximum(np.abs(x), 1e-12)), floor)


def envelope(pcm: Pcm, hop: float = HOP, start: float = 0.0, end: float | None = None) -> np.ndarray:
    """Loudness outline: RMS level in dBFS for every `hop` seconds (mono mix)."""
    n = max(1, int(round(hop * pcm.rate)))
    out: list[np.ndarray] = []
    carry = np.zeros(0, dtype=np.float32)
    for _, mono in pcm.mono_blocks(start, end):
        joined = np.concatenate([carry, mono]) if carry.size else mono
        whole = len(joined) // n * n
        if whole:
            frames = joined[:whole].reshape(-1, n)
            out.append(np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)))
        carry = joined[whole:]
    if carry.size:
        out.append(np.array([np.sqrt(np.mean(carry.astype(np.float64) ** 2))]))
    rms = np.concatenate(out) if out else np.zeros(0)
    return db(rms).astype(np.float32)


def peaks(pcm: Pcm, buckets: int = 1200, start: float = 0.0, end: float | None = None) -> dict:
    """Drawing outline: the loudest sample (0..1) in each of `buckets` slices."""
    total_end = pcm.duration if end is None else min(end, pcm.duration)
    span = max(0.0, total_end - start)
    buckets = max(10, min(int(buckets), 20000))
    if span <= 0:
        return {"duration": round(pcm.duration, 3), "start": start, "end": total_end, "peaks": [0.0] * buckets}
    edges = np.linspace(0, span * pcm.rate, buckets + 1).astype(np.int64)
    result = np.zeros(buckets, dtype=np.float32)
    first = int(round(start * pcm.rate))
    for pos, mono in pcm.mono_blocks(start, total_end):
        a, b = pos - first, pos - first + len(mono)
        lo = max(0, int(np.searchsorted(edges, a, side="right")) - 1)
        hi = min(buckets, int(np.searchsorted(edges, b, side="left")))
        absval = np.abs(mono)
        for k in range(lo, hi):
            s, e = max(edges[k], a) - a, min(edges[k + 1], b) - a
            if e > s:
                result[k] = max(result[k], float(absval[s:e].max()))
    return {
        "duration": round(pcm.duration, 3),
        "start": round(start, 3),
        "end": round(total_end, 3),
        "peaks": [round(float(v), 4) for v in np.minimum(result, 1.0)],
    }


def speech_level(env: np.ndarray) -> float:
    """A robust "how loud is talking" level in dBFS: the 90th percentile of the outline."""
    voiced = env[env > -90]
    return float(np.percentile(voiced, 90)) if voiced.size else -90.0


def noise_floor(env: np.ndarray) -> float:
    """The quiet between words, in dBFS: the 10th percentile of the outline."""
    voiced = env[env > -110]
    return float(np.percentile(voiced, 10)) if voiced.size else -110.0


def find_silences(env: np.ndarray, threshold_db: float, min_len: float, hop: float = HOP) -> list[tuple[float, float]]:
    """Stretches quieter than `threshold_db` lasting at least `min_len` seconds."""
    quiet = np.concatenate([[False], env < threshold_db, [False]])
    edges = np.flatnonzero(np.diff(quiet.astype(np.int8)))
    runs = edges.reshape(-1, 2)
    out = []
    for a, b in runs:
        if (b - a) * hop >= min_len:
            out.append((round(a * hop, 3), round(b * hop, 3)))
    return out


def dead_air_cuts(silences: list[tuple[float, float]], *, longer_than: float, keep: float,
                  duration: float | None = None) -> list[tuple[float, float]]:
    """Shorten each silence longer than `longer_than` seconds to `keep` seconds by cutting its middle.
    Silences touching the start or end of the recording are trimmed the same way."""
    cuts = []
    for a, b in silences:
        if b - a <= longer_than:
            continue
        half = keep / 2.0
        ca, cb = a + half, b - half
        if duration is not None:
            cb = min(cb, duration)
        if cb - ca >= 0.05:
            cuts.append((round(ca, 3), round(cb, 3)))
    return cuts


def suggest_threshold(env: np.ndarray) -> float:
    """Where "silence" starts for this recording: 40% of the way from the noise floor up to the
    speech level, but at least 18 dB under the speech and at least 4 dB above the floor."""
    floor, speech = noise_floor(env), speech_level(env)
    if speech <= floor + 8:
        return round(floor + 4, 1)
    guess = min(floor + 0.4 * (speech - floor), speech - 18.0)
    return round(max(guess, floor + 4.0), 1)


# -------------------------------------------------------------------- syncing
def offset_by_sound(ref: np.ndarray, other: np.ndarray, hop: float = HOP, max_shift: float = 30.0) -> tuple[float, float]:
    """How far `other` lags `ref` (seconds; positive = starts later), from their loudness outlines,
    plus a 0..1 confidence. Uses FFT cross-correlation of the normalized outlines."""
    def prep(env: np.ndarray) -> np.ndarray:
        x = np.maximum(env.astype(np.float64), -80.0)
        x = x - x.mean()
        sd = x.std()
        return x / sd if sd > 1e-9 else x

    a, b = prep(ref), prep(other)
    n = 1
    while n < len(a) + len(b):
        n *= 2
    corr = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    lags = np.concatenate([np.arange(0, n // 2), np.arange(-n // 2, 0)])
    limit = int(max_shift / hop)
    mask = np.abs(lags) <= limit
    best = int(np.argmax(np.where(mask, corr, -np.inf)))
    overlap = max(1, min(len(a), len(b)))
    confidence = float(np.clip(corr[best] / overlap, 0.0, 1.0))
    return round(lags[best] * hop, 3), round(confidence, 3)


# ------------------------------------------------------------------- editing
def crossfade(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Equal-power blend from the end of `a` into the start of `b` (same length)."""
    t = np.linspace(0.0, np.pi / 2, len(a), dtype=np.float32)[:, None] if a.ndim == 2 else np.linspace(0.0, np.pi / 2, len(a), dtype=np.float32)
    return a * np.cos(t) + b * np.sin(t)
