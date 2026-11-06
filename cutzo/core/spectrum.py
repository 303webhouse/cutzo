"""The New Heretics' audio spectrum, drawn the way After Effects drew it for Nick.

Nick rendered the spectrum for each episode in After Effects with its Audio Spectrum effect and laid
it over the background in Premiere. From his template (Audio Spectrum Template.aep, read Oct 15,
2026): the frequencies from 20 to 2300 Hz in 400 bands, 90 ms of sound per frame, each band drawn
as a soft line across a straight path, on both sides of it, as long as that band is loud.

How long the lines are for a given sound, the colours and the timing were then matched against the
finished JW Ghost Stories video (4 stretches of talk, 124 frames, every pixel column):
- a band's line reaches 480 x amplitude^0.7 pixels from the path at 1080p (amplitude 1.0 = a
  full-scale sine at that frequency), measured over a 90 ms Hann window; the lengths agree with
  the real ones with 0.75 correlation, column by column (other windows and scales did worse);
- the sound shown is centred 97.5 ms after the frame;
- each column of pixels shows its tallest band, so the tops step like a bar chart (the real tops
  are a little more jagged still);
- the shape is a see-through fill (about 75% solid) in orange at both ends of the path (hue 30)
  and yellow in the middle (hue 58), with a brighter, yellower rim along its edges and a bright
  line along the path.
That video's talk measured -16.0 LUFS, so Cutzo feeds the spectrum sound at that loudness.

The 1920x1080 render sat at 100% in his 4K sequence, centred 35.7% across and 84.5% down, so in the
finished 1080p frame the path runs from x 238.6 to 1142.6 at y 913.2.

Cutzo works out, for every frame, how solid each pixel of the fill and of the rim is (two grey
pictures stacked in one, sent to FFmpeg as it goes); FFmpeg gets the two layers' colours once, as
still pictures, since a pixel's colour depends only on where it is.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from . import audio

FPS = 30000 / 1001
TALK_LUFS = -16.0  # the talk's loudness in the video the spectrum was matched against


@dataclass(frozen=True)
class Style:
    """The spectrum's settings at 1080p (Cutzo scales them for other frame sizes)."""
    start_hz: float = 20.0
    end_hz: float = 2300.0
    bands: int = 400
    window: float = 0.090  # seconds of sound behind each frame ("Audio Duration", 90 ms)
    window_shape: str = "hann"  # "hann" or "rect"
    lead: float = 0.0975  # the sound shown is centred this long after the frame
    height: float = 480.0  # a band's line reaches height x amplitude^power pixels from the path
    power: float = 0.7
    thickness: float = 3.85  # line thickness (7.7 in the half-size render)
    softness: float = 0.5
    fill_hue_ends: float = 30.0  # degrees: orange at both ends of the path...
    fill_hue_middle: float = 58.0  # ...yellow in the middle
    fill_saturation: float = 0.82
    fill_opacity_ends: float = 0.70
    fill_opacity_middle: float = 0.80
    ripple: float = 0.15  # the fill is a little thinner between bands (fine stripes)
    rim_hue_shift: float = 12.0  # the rim is yellower than the fill under it...
    rim_saturation: float = 0.62  # ...and paler
    rim_width: float = 1.1  # pixels
    rim_opacity: float = 0.9
    line_opacity: float = 0.9  # the bright line along the path
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
        self.r = r = s.thickness * k / 2  # half thickness
        x0, x1, y = s.x0 * k, s.x1 * k, s.y * k
        self.limit = limit = s.limit * k
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
        spacing = (x1 - x0) / (s.bands - 1)
        cols = left + np.arange(width) + 0.5
        place = (cols - x0) / (x1 - x0)
        self._place = np.clip(place, 0.0, 1.0)
        # Which bands' lines cover each pixel column: those whose centre is within the half
        # thickness (plus half a pixel). A column shows the tallest of them.
        nearest = np.clip(np.round(place * (s.bands - 1)).astype(int), 0, s.bands - 1)
        self._idx = np.clip(nearest[:, None] + np.arange(-2, 3)[None, :], 0, s.bands - 1)  # (width, 5)
        centres = x0 + self._idx * spacing
        self._near = (np.abs(cols[:, None] - centres) <= r + 0.5).astype(np.float32)
        self._near[(place < -0.002) | (place > 1.002)] = 0.0  # past the ends of the path: nothing
        covered = self._near.max(axis=1) > 0
        # Fine stripes: the fill is solid at a band's centre and thinner half-way to the next.
        gap = np.abs(cols - (x0 + np.round(place * (s.bands - 1)) * spacing)) / (spacing / 2)
        middle = 1.0 - np.abs(2.0 * self._place - 1.0)  # 0 at both ends of the path, 1 in the middle
        opacity = s.fill_opacity_ends + (s.fill_opacity_middle - s.fill_opacity_ends) * middle
        self._fill = (opacity * (1.0 - s.ripple * np.clip(gap, 0, 1) ** 2) * covered).astype(np.float32)
        self._covered = covered
        self._rows = (top + np.arange(height) + 0.5 - y).astype(np.float32)  # distance from the path
        # Frequency analysis: a window over `window` seconds, read between FFT bins at each band.
        self._n = int(round(s.window * self.rate))
        shape = np.hanning(self._n) if s.window_shape == "hann" else np.ones(self._n)
        self._win = shape.astype(np.float32)
        bins = np.fft.rfftfreq(self._n, 1.0 / self.rate)
        pos = np.interp(np.linspace(s.start_hz, s.end_hz, s.bands), bins, np.arange(len(bins)))
        self._lo = np.floor(pos).astype(int)
        self._frac = (pos - self._lo).astype(np.float32)
        self._norm = 2.0 / float(self._win.sum())

    # ------------------------------------------------------------------ colours
    def _hues(self) -> np.ndarray:
        s = self.style
        middle = 1.0 - np.abs(2.0 * self._place - 1.0)
        return s.fill_hue_ends + (s.fill_hue_middle - s.fill_hue_ends) * middle

    def _picture(self, hues: np.ndarray, saturation: float) -> np.ndarray:
        row = np.array([colorsys.hsv_to_rgb((h / 360.0) % 1.0, saturation, 1.0) for h in hues]) * 255.0
        return np.repeat(np.round(row).astype(np.uint8)[None, :, :], self.box.height, axis=0)

    def fill_colours(self) -> np.ndarray:
        """The fill's colours: an RGB picture the size of the box, the same in every frame."""
        return self._picture(self._hues(), self.style.fill_saturation)

    def rim_colours(self) -> np.ndarray:
        """The rim's colours (and the line along the path), the same in every frame."""
        return self._picture(self._hues() + self.style.rim_hue_shift, self.style.rim_saturation)

    # ------------------------------------------------------------------ pictures
    def heights(self, windows: np.ndarray) -> np.ndarray:
        """How far each band's line reaches from the path, for a batch of sound windows
        (frames x samples, mono)."""
        spec = np.fft.rfft(windows * self._win[None, :], axis=1)
        mag = np.abs(spec).astype(np.float32) * self._norm
        amp = mag[:, self._lo] * (1 - self._frac) + mag[:, self._lo + 1] * self._frac
        return np.minimum(self.style.height * self.k * amp ** self.style.power, self.limit)

    def planes(self, heights: np.ndarray, visible: float = 1.0) -> np.ndarray:
        """How solid each pixel of the fill (top half) and of the rim (bottom half) is, 0-255, for
        one frame's band lengths. Quiet bands still make a short dot, so silence is a line."""
        h, w = self.box.height, self.box.width
        out = np.zeros((2 * h, w), dtype=np.uint8)
        if visible <= 0:
            return out
        s = self.style
        length = np.maximum((heights[self._idx] * self._near).max(axis=1), self.r)  # per column
        length[~self._covered] = 0.0
        reach = float(length.max()) + 2.0
        rows = np.flatnonzero(np.abs(self._rows) <= reach)
        a, b = rows[0], rows[-1] + 1
        dy = np.abs(self._rows[a:b])[:, None]
        feather = max(self.r * s.softness, 0.5)
        inside = np.clip((length[None, :] - dy) / feather + 0.5, 0.0, 1.0)
        fill = inside * self._fill[None, :]
        # The rim: along the end of each column's line, and down the side of a column that sticks
        # out past its neighbour.
        rim_w = s.rim_width * self.k
        edge = np.clip(1.0 - np.abs(length[None, :] - dy - rim_w / 2) / rim_w, 0.0, 1.0)
        shorter = np.minimum(np.roll(length, 1), np.roll(length, -1))
        side = ((dy > shorter[None, :] - rim_w / 2) & (dy <= length[None, :])).astype(np.float32) * inside
        rim = np.maximum(edge, side) * s.rim_opacity
        line = np.clip(1.0 - dy / (1.2 * self.k), 0.0, 1.0) * s.line_opacity * self._covered[None, :]
        rim = np.maximum(rim, line)
        scale = 255.0 * min(1.0, visible)
        out[a:b] = np.round(fill * scale).astype(np.uint8)
        out[h + a:h + b] = np.round(rim * scale).astype(np.uint8)
        return out

    # ------------------------------------------------------------------ frames
    def frames(self, sound: Callable[[int, int], np.ndarray], total_frames: int,
               visibility: Callable[[float], float], *, fps: float = FPS, batch: int = 128, start: int = 0,
               check: Callable[[], None] | None = None) -> Iterator[bytes]:
        """Each frame's two stacked grey pictures, in order, from frame `start` up to `total_frames`.
        `sound(a, b)` returns mono samples a..b (zeros outside the recording); `visibility(t)` says
        how visible the spectrum is at time t (0-1)."""
        half = self._n // 2
        blank = bytes(2 * self.box.width * self.box.height)
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
                yield self.planes(h, v).tobytes() if v > 0 else blank


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
