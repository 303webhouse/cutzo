"""Sound effects made on a copy of a clip before mixing.

"tv": Nick's "From the television" preset in Premiere, used on The Dig's cold open and sign-off clips so
listeners hear them as a preview or a bonus rather than the show itself. The curve was measured on the
cold open of his Season Debrief Part 2 MP3 against the same words in the raw recording (Nov 5, 2026,
F:\\Cutzo\\lab\\tv_measure.py): the low end gone below ~300 Hz, a bump around 1 kHz, nothing much above
~4 kHz. The FFmpeg chain below matches it within about 1.5 dB from 160 Hz to 5 kHz; a little squeeze
makes it sound like a small speaker. The copy keeps the clip's loudness, so the mix levels still hold.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..tools import ffmpeg
from . import audio

CHAINS = {
    "tv": ("highpass=f=350:p=2,equalizer=f=280:t=o:w=1.5:g=-6,equalizer=f=1050:t=o:w=1.0:g=3.5,"
           "lowpass=f=3400:p=2,lowpass=f=3400:p=2,"
           "acompressor=threshold=-22dB:ratio=3:attack=5:release=80"),
}
VERSION = 1  # change when a chain changes, so old copies aren't reused
PAD = 0.25  # seconds of sound either side, so the filters settle before the clip starts


def clip(effect: str, voice_path: Path, start: float, end: float, folder: Path, *, check=None) -> tuple[Path, float, float]:
    """A copy of voice_path[start:end] (mono working audio) with the effect, kept in `folder` for next time.
    Returns (file, start, end) to play from it."""
    chain = CHAINS[effect]
    pcm = audio.Pcm(voice_path)
    a, b = max(0.0, start - PAD), min(pcm.duration, end + PAD)
    name = f"{effect}{VERSION}-{int(round(start * 1000))}-{int(round(end * 1000))}"
    dst = folder / f"{name}.f32"
    if not dst.exists():
        folder.mkdir(parents=True, exist_ok=True)
        raw = folder / f"{name}.raw.f32"
        tmp = folder / f"{name}.part.f32"
        try:
            rate = audio.RATE
            np.asarray(pcm.open()[int(a * rate):int(b * rate), 0], dtype="<f4").tofile(raw)
            ffmpeg.filter_raw(raw, tmp, chain, check=check)
            before = ffmpeg.loudness(raw, check=check)["integrated"]
            after = ffmpeg.loudness(tmp, check=check)["integrated"]
            if before > -70 and after > -70:  # not silence
                y = np.fromfile(tmp, dtype="<f4") * np.float32(10 ** ((before - after) / 20))
                y.astype("<f4").tofile(tmp)
            tmp.replace(dst)
        finally:
            raw.unlink(missing_ok=True)
            tmp.unlink(missing_ok=True)
    return dst, start - a, end - a
