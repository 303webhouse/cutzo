"""The New Heretics' audio spectrum, drawn the way After Effects drew it for Nick.

Nick rendered the spectrum for each episode in After Effects with its Audio Spectrum effect and laid
it over the background in Premiere. From his template (Audio Spectrum Template.aep, read Oct 15,
2026): the frequencies from 20 to 2300 Hz in 400 bands, 90 ms of sound per frame, each band drawn
as a soft line across a straight path, on both sides of it, as long as that band is loud.

How long the lines are for a given sound, the colours and the timing were then matched against the
finished JW Ghost Stories video (4 stretches of talk, 124 frames, every pixel column): a band's
line reaches 577.6 x amplitude^0.7 pixels from the path at 1080p (amplitude 1.0 = a full-scale
sine at that frequency; the shapes agree with 0.83 correlation), and the sound shown is centred
97.5 ms after the frame. The colour is orange (hue 24 degrees) at both ends of the path and golden
yellow (hue 55) in the middle, a little more orange towards the tips, and slightly see-through.
That video's talk measured -16.0 LUFS, so Cutzo feeds the spectrum sound at that loudness.

The 1920x1080 render sat at 100% in his 4K sequence, centred 35.7% across and 84.5% down, so in the
finished 1080p frame the path runs from x 238.6 to 1142.6 at y 913.2.

Cutzo draws only how see-through each pixel is (a grey picture per frame, sent to FFmpeg as it
goes) and gives FFmpeg the colours once, as a still picture: the colour of a pixel depends only on
where it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from . import audio

FPS = 30000 / 1001
TALK_LUFS = -16.0  # the talk's loudness in the video the spectrum was matched against


@dataclass(frozen=True)
class Style:
    """The spectrum's settings at 1080p (Cutzo scales the placement for other frame sizes)."""
    start_hz: float = 20.0
    end_hz: float = 2300.0
    bands: int = 400
    window: float = 0.090  # seconds of sound behind each frame ("Audio Duration", 90 ms)
    lead: float = 0.0975  # the sound shown is centred this long after the frame
    height: float = 577.6  # a band's line reaches height x amplitude^power pixels from the path
    power: float = 0.7
    thickness: float = 3.85  # line thickness (7.7 in the half-size render)
    softness: float = 0.5
    hue_ends: float = 24.0  # degrees: orange at both ends of the path...
    hue_middle: float = 55.0  # ...golden yellow in the middle
    hue_tips: float = -6.0  # a little more orange towards the ends of long lines
    saturation: float = 0.82
    value: float = 0.98
    opacity: float = 0.92
    x0: float = 238.6
    x1: float = 1142.6
    y: float = 913.2
    limit: float = 130.0  # longest line, from the path (keeps the box small)


@dataclass
class Box:
    """Where the spectrum's picture goes in the frame (top-left corner, even sizes for FFmpeg)."""
    x: int
    y: int
    width: int
    height: int


@dataclass
class Spectrum:
    style: Style = field(default_factory=Style)
    rate: int = audio.RATE
    frame_height: int = 1080

    def __post_init__(self) -> None:
        s = self.style
        k = self.frame_height / 1080.0
        self.k = k
        r = s.thickness * k / 2  # half thickness
        self.r = r
        x0, x1, y = s.x0 * k, s.x1 * k, s.y * k
        limit = s.limit * k
        left = int(np.floor(x0 - 2 * r)) - 2
        right = int(np.ceil(x1 + 2 * r)) + 2
        top = int(np.floor(y - limit - 2 * r)) - 2
        bottom = int(np.ceil(y + limit + 2 * r)) + 2
        top -= top % 2
        left -= left % 2
        width, height = right - left, bottom - top
        width += width % 2
        height += height % 2
        self.box = Box(left, top, width, height)
        self.limit = limit
        centres = x0 + (x1 - x0) * np.arange(s.bands) / (s.bands - 1)
        # Each pixel column is covered by the two or three nearest bands' soft lines. Sample four
        # points across the column for how much of it each covers (1 in the core, fading to 0 at
        # the edge: the "softness").
        cols = left + np.arange(width)
        sub = cols[:, None] + (np.arange(4)[None, :] + 0.5) / 4  # (width, 4)
        nearest = np.clip(np.round((cols + 0.5 - x0) / (x1 - x0) * (s.bands - 1)).astype(int), 0, s.bands - 1)
        idx = np.clip(nearest[:, None] + np.arange(-3, 4)[None, :], 0, s.bands - 1)  # (width, 7)
        d = np.abs(sub[:, None, :] - centres[idx][:, :, None]) / r  # distance in half-thicknesses
        if s.softness > 0:
            cover = np.where(d <= 1.0 - s.softness, 1.0, np.clip((1.0 - d) / s.softness, 0.0, 1.0))
        else:
            cover = (d <= 1.0).astype(float)
        w = cover.mean(axis=2)  # (width, 7): how much of the column each nearby band covers
        dup = np.zeros_like(w, dtype=bool)  # the same band twice near the ends: count it once
        dup[:, 1:] = idx[:, 1:] == idx[:, :-1]
        w[dup] = 0.0
        total = w.sum(axis=1)
        self._idx = idx
        self._cover = np.clip(total, 0.0, 1.0).astype(np.float32)
        self._wn = (w / np.where(total > 0, total, 1.0)[:, None]).astype(np.float32)
        self._place = np.clip((cols + 0.5 - x0) / (x1 - x0), 0.0, 1.0)  # 0..1 along the path
        self._rows = (top + np.arange(height) + 0.5 - y).astype(np.float32)  # distance from the path
        # Frequency analysis: a Hann window over `window` seconds, read between FFT bins at each band.
        self._n = int(round(s.window * self.rate))
        self._hann = np.hanning(self._n).astype(np.float32)
        bins = np.fft.rfftfreq(self._n, 1.0 / self.rate)
        pos = np.interp(np.linspace(s.start_hz, s.end_hz, s.bands), bins, np.arange(len(bins)))
        self._lo = np.floor(pos).astype(int)
        self._frac = (pos - self._lo).astype(np.float32)
        self._norm = 2.0 / float(self._hann.sum())

    # ------------------------------------------------------------------ pictures
    def colour_picture(self) -> np.ndarray:
        """The colours as an RGB picture the size of the box (the same in every frame)."""
        s = self.style
        middle = 1.0 - np.abs(2.0 * self._place - 1.0)  # 0 at both ends of the path, 1 in the middle
        hue = s.hue_ends + (s.hue_middle - s.hue_ends) * middle  # per column
        reach = np.clip(np.abs(self._rows) / (30.0 * self.k), 0.0, 1.0)  # per row: towards the tips
        h = (hue[None, :] + s.hue_tips * reach[:, None]) / 360.0 % 1.0
        out = np.empty((self.box.height, self.box.width, 3), dtype=np.uint8)
        # HSV -> RGB for the whole picture (numpy version of colorsys.hsv_to_rgb)
        i = np.floor(h * 6.0).astype(int) % 6
        f = h * 6.0 - np.floor(h * 6.0)
        v, sat = s.value, s.saturation
        p, q, t = v * (1 - sat), v * (1 - sat * f), v * (1 - sat * (1 - f))
        rgb = np.select([i[..., None] == n for n in range(6)],
                        [np.stack(c, axis=-1) for c in (
                            (np.full_like(h, v), t, np.full_like(h, p)), (q, np.full_like(h, v), np.full_like(h, p)),
                            (np.full_like(h, p), np.full_like(h, v), t), (np.full_like(h, p), q, np.full_like(h, v)),
                            (t, np.full_like(h, p), np.full_like(h, v)), (np.full_like(h, v), np.full_like(h, p), q))])
        out[:] = np.round(rgb * 255.0).clip(0, 255).astype(np.uint8)
        return out

    def heights(self, windows: np.ndarray) -> np.ndarray:
        """How far each band's line reaches from the path, for a batch of sound windows
        (frames x samples, mono)."""
        spec = np.fft.rfft(windows * self._hann[None, :], axis=1)
        mag = np.abs(spec).astype(np.float32) * self._norm
        amp = mag[:, self._lo] * (1 - self._frac) + mag[:, self._lo + 1] * self._frac
        return np.minimum(self.style.height * self.k * amp ** self.style.power, self.limit)

    def alpha(self, heights: np.ndarray, visible: float = 1.0) -> np.ndarray:
        """How see-through each pixel of the box is (0-255) for one frame's band lengths. Quiet
        bands still show as a short dot, so silence is a dotted line."""
        out = np.zeros((self.box.height, self.box.width), dtype=np.uint8)
        if visible <= 0:
            return out
        length = np.maximum((heights[self._idx] * self._wn).sum(axis=1), self.r)  # per column
        reach = float(length.max()) + 2.0
        rows = np.flatnonzero(np.abs(self._rows) <= reach)
        if not len(rows):
            return out
        a, b = rows[0], rows[-1] + 1
        feather = max(self.r * self.style.softness * 2, 0.5)
        dy = np.abs(self._rows[a:b])[:, None]
        cover = np.clip((length[None, :] - dy) / feather + 0.5, 0.0, 1.0) * self._cover[None, :]
        out[a:b] = np.round(cover * (255.0 * self.style.opacity * min(1.0, visible))).astype(np.uint8)
        return out

    # ------------------------------------------------------------------ frames
    def frames(self, sound: Callable[[int, int], np.ndarray], total_frames: int,
               visibility: Callable[[float], float], *, fps: float = FPS, batch: int = 128, start: int = 0,
               check: Callable[[], None] | None = None) -> Iterator[bytes]:
        """Each frame's grey picture, in order, from frame `start` up to `total_frames`. `sound(a, b)`
        returns mono samples a..b (zeros outside the recording); `visibility(t)` says how visible
        the spectrum is at time t (0-1)."""
        half = self._n // 2
        blank = bytes(self.box.width * self.box.height)
        for first in range(start, total_frames, batch):
            if check:
                check()
            ks = np.arange(first, min(total_frames, first + batch))
            seen = [visibility(k / fps) for k in ks]
            if not any(v > 0 for v in seen):
                for _ in ks:
                    yield blank
                continue
            centres = np.round((ks / fps + self.style.lead) * self.rate).astype(np.int64)
            a, b = int(centres[0]) - half, int(centres[-1]) - half + self._n
            chunk = sound(a, b)
            windows = np.lib.stride_tricks.sliding_window_view(chunk, self._n)[centres - half - a]
            heights = self.heights(np.asarray(windows, dtype=np.float32))
            for h, v in zip(heights, seen):
                yield self.alpha(h, v).tobytes() if v > 0 else blank


def mono_reader(path: Path, channels: int = 2, gain_db: float = 0.0) -> Callable[[int, int], np.ndarray]:
    """sound(a, b) over a Cutzo working file: mono samples a..b, zeros outside it."""
    data = audio.Pcm(path, channels=channels).open()
    n = len(data)
    gain = np.float32(10 ** (gain_db / 20.0))

    def sound(a: int, b: int) -> np.ndarray:
        out = np.zeros(b - a, dtype=np.float32)
        lo, hi = max(a, 0), min(b, n)
        if hi > lo:
            block = np.asarray(data[lo:hi], dtype=np.float32)
            out[lo - a:hi - a] = (block.mean(axis=1) if block.ndim == 2 else block) * gain
        return out

    return sound


def windows_visibility(windows: list[tuple[float, float, float, float]]) -> Callable[[float], float]:
    """visibility(t) from (start, end, fade in, fade out) windows."""
    spans = sorted(windows)

    def visible(t: float) -> float:
        best = 0.0
        for a, b, fi, fo in spans:
            if a <= t < b:
                v = 1.0
                if fi > 0:
                    v = min(v, (t - a) / fi)
                if fo > 0:
                    v = min(v, (b - t) / fo)
                best = max(best, v)
        return max(0.0, min(1.0, best))

    return visible
