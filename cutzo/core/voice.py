"""Podcast voice: Cutzo's cleanup chain for talking.

    1. Rumble filter        high-pass at 80 Hz (FFmpeg)
    2. Noise removal        DeepFilterNet, with a limit on how far the background is turned down
    3. Finishing            de-essing, gentle compression and leveling (FFmpeg)

Everything runs on working files (.f32, mono, 48 kHz) and keeps the timing exactly,
so cuts made on the original recording still line up. Long recordings are cleaned in
pieces on several processor cores at once, then joined with short crossfades.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..tools import deepfilter, ffmpeg
from . import audio

PIECE = 300.0  # seconds of audio per piece
PREROLL = 2.0  # seconds before each piece, so the model has settled when the piece starts
TAIL = 1.0  # seconds after each piece (DeepFilterNet's output ends 30 ms early)
FADE = 0.02  # seconds of crossfade where pieces join


@dataclass(frozen=True)
class VoiceSettings:
    strength: int = 70  # 0 = no noise removal, 100 = as much as the model can
    rumble_hz: int = 80  # 0 = off
    deess: bool = True
    compress: bool = True
    level: bool = True
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "VoiceSettings":
        data = dict(data or {})
        return cls(
            strength=int(max(0, min(100, data.get("strength", 70)))),
            rumble_hz=int(max(0, min(200, data.get("rumble_hz", 80)))),
            deess=bool(data.get("deess", True)),
            compress=bool(data.get("compress", True)),
            level=bool(data.get("level", True)),
        )

    @property
    def limit_db(self) -> float:
        """How far DeepFilterNet may turn the background down: 0..48 dB, or no limit at 100."""
        return 100.0 if self.strength >= 100 else round(self.strength * 0.48, 1)

    def steps(self) -> list[str]:
        out = []
        if self.rumble_hz:
            out.append(f"Rumble filter ({self.rumble_hz} Hz)")
        if self.strength:
            out.append(f"Noise removal (strength {self.strength} of 100)")
        if self.deess:
            out.append("De-essing")
        if self.compress:
            out.append("Gentle compression")
        if self.level:
            out.append("Leveling between voices")
        return out or ["Nothing (all cleanup switched off)"]

    def pre_filters(self) -> str | None:
        return f"highpass=f={self.rumble_hz}:poles=2" if self.rumble_hz else None

    def post_filters(self) -> str | None:
        parts = []
        if self.deess:
            parts.append("deesser=i=0.4:m=0.5:f=0.5:s=o")
        if self.compress:
            parts.append("acompressor=threshold=-24dB:ratio=2.5:attack=8:release=200:knee=6:makeup=2dB")
        if self.level:
            parts.append("dynaudnorm=f=400:g=15:p=0.7:m=6:r=0.3")
        return ",".join(parts) or None


Progress = Callable[[float, str], None]


def clean(src: Path, dst: Path, settings: VoiceSettings, *, work_dir: Path, progress: Progress | None = None,
          check: Callable[[], None] | None = None, workers: int | None = None) -> Path:
    """Run the whole chain on a mono working file `src` into `dst` (same length, same timing)."""
    pcm = audio.Pcm(src)
    total = pcm.duration

    def report(share: float, label: str) -> None:
        if progress:
            progress(max(0.0, min(1.0, share)), label)

    current = src
    temp: list[Path] = []
    try:
        if settings.pre_filters():
            report(0.0, "Rumble filter")
            step = work_dir / f"{dst.stem}.pre.f32"
            ffmpeg.filter_raw(current, step, settings.pre_filters(), duration=total, check=check,
                              progress=lambda p: report(0.05 * p, "Rumble filter"))
            temp.append(step)
            current = step
        if settings.strength:
            step = work_dir / f"{dst.stem}.dfn.f32"
            denoise_long(current, step, limit_db=settings.limit_db, work_dir=work_dir, check=check, workers=workers,
                         progress=lambda p: report(0.05 + 0.8 * p, "Noise removal"))
            temp.append(step)
            current = step
        post = settings.post_filters()
        if post:
            ffmpeg.filter_raw(current, dst, post, duration=total, check=check,
                              progress=lambda p: report(0.85 + 0.15 * p, "De-essing, compression and leveling"))
        elif current != dst:
            shutil.copyfile(current, dst)
        _match_length(dst, pcm.frames)
        report(1.0, "Done")
        return dst
    finally:
        for path in temp:
            if path != dst:
                path.unlink(missing_ok=True)


def _match_length(path: Path, frames: int) -> None:
    """Pad or trim a working file to exactly `frames` samples (filters can be a few samples off)."""
    have = audio.Pcm(path).frames
    if have == frames:
        return
    if have > frames:
        with open(path, "r+b") as fh:
            fh.truncate(frames * 4)
    else:
        with open(path, "ab") as fh:
            fh.write(np.zeros(frames - have, dtype="<f4").tobytes())


def denoise_long(src: Path, dst: Path, *, limit_db: float, work_dir: Path, progress: Callable[[float], None] | None = None,
                 check: Callable[[], None] | None = None, workers: int | None = None, piece: float = PIECE) -> Path:
    """DeepFilterNet over a long working file, in pieces on several cores, joined seamlessly."""
    pcm = audio.Pcm(src)
    rate, frames = pcm.rate, pcm.frames
    data = pcm.open()[:, 0]
    starts = list(np.arange(0, frames, int(piece * rate)))
    workers = workers or max(1, min(len(starts), (os.cpu_count() or 2) // 2, 8))
    pre, tail, fade = int(PREROLL * rate), int(TAIL * rate), int(FADE * rate)
    done = [0.0] * len(starts)
    lock = threading.Lock()
    out = np.memmap(dst, dtype="<f4", mode="w+", shape=(frames,)) if frames else None
    if out is None:
        dst.write_bytes(b"")
        return dst

    def one(k: int) -> tuple[int, np.ndarray, int]:
        a = starts[k]
        b = min(frames, a + int(piece * rate))
        lo, hi = max(0, a - pre), min(frames, b + tail)
        chunk = np.asarray(data[lo:hi], dtype=np.float32)
        if hi == frames:  # the model's output ends 30 ms early: pad so nothing is lost
            chunk = np.concatenate([chunk, np.zeros(int(0.1 * rate), dtype=np.float32)])
        with tempfile.TemporaryDirectory(dir=work_dir, prefix=f"piece{k}-") as tmp:
            wav_in = Path(tmp) / f"piece{k}.wav"
            _write_wav(wav_in, chunk, rate)
            wav_out = Path(tmp) / f"piece{k}-clean.wav"

            def piece_progress(p: float) -> None:
                with lock:
                    done[k] = p
                    if progress:
                        progress(sum(done) / len(done))

            deepfilter.denoise(wav_in, wav_out, limit_db=limit_db, duration=len(chunk) / rate,
                               progress=piece_progress, check=check)
            cleaned, _ = audio.read_wav(wav_out)
        return k, cleaned[:, 0], lo

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cutzo-dfn") as pool:
        results = list(pool.map(one, range(len(starts))))
    for k, cleaned, lo in sorted(results):
        a = starts[k]
        b = min(frames, a + int(piece * rate))
        seg = cleaned[a - lo: a - lo + (b - a)]
        if len(seg) < b - a:  # should not happen, but never leave a gap of garbage
            seg = np.concatenate([seg, np.asarray(data[a + len(seg):b], dtype=np.float32)])
        if k > 0 and fade:
            # Blend across the join using the previous piece's tail, which overlaps this piece.
            prev_k, prev_clean, prev_lo = results[k - 1]
            f = min(fade, len(seg))
            prev_tail = prev_clean[a - prev_lo: a - prev_lo + f]
            if len(prev_tail) == f:
                seg = seg.copy()
                seg[:f] = audio.crossfade(prev_tail, seg[:f])
        out[a:b] = seg
    out.flush()
    del out
    if progress:
        progress(1.0)
    return dst


def _write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    """32-bit float mono WAV (what DeepFilterNet reads)."""
    import struct

    data = np.asarray(samples, dtype="<f4").tobytes()
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 3, 1, rate, rate * 4, 4, 32)
    header += b"data" + struct.pack("<I", len(data))
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(data)
