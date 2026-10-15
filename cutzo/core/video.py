"""Making the finished video for shows with a picture (The New Heretics).

The recipe (recipes.py) says what the frame shows over time: layers of kit videos, a still, the
episode title graphic, and when the spectrum shows. Here FFmpeg puts it together in one pass:

    a black 1080p canvas
      + each layer on top, at its time (kit videos come from 1080p copies made once, see
        kit.video_copy), dissolving in and out where the recipe says
      + the spectrum: Cutzo draws it frame by frame (spectrum.py) and feeds it to FFmpeg as it goes
      + the fade to black at the end
    with the episode's sound, already encoded and checked for loudness, copied in.

Long layers that fade are split into a fading start, a plain middle and a fading end, so FFmpeg
only blends see-through pictures where it has to.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import IO, Any, Callable

import numpy as np

from ..tools import ffmpeg, require
from . import kit, render, spectrum as spectrumlib, titles

FPS = "30000/1001"
FPS_F = 30000 / 1001
SPLIT_FADES_LONGER_THAN = 4.0  # seconds of plain picture worth a separate, non-blended part
LOOP_STILLS = True  # pictures (title, spectrum colours) converted once and held, not re-read every frame

KitVideo = Callable[[str, float], Path]  # (asset id, seconds needed from its start) -> 1080p copy

_nvenc: bool | None = None


def nvenc_available() -> bool:
    """Whether this computer's graphics card can encode H.264 for FFmpeg (NVIDIA NVENC). Tried once."""
    global _nvenc
    if _nvenc is None:
        try:
            ffmpeg.run(["-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=0.2", "-c:v", "h264_nvenc",
                        "-f", "null", "-"], keep_lines=5)
            _nvenc = True
        except Exception:  # noqa: BLE001 - any failure means: use the processor instead
            _nvenc = False
    return _nvenc


def encoder_args(video: dict[str, Any]) -> list[str]:
    """H.264 High at the target's bitrate, keyframes every `gop` frames, BT.709 colour."""
    kbps, top = int(video["bitrate_kbps"]), int(video.get("max_kbps", video["bitrate_kbps"] * 1.5))
    gop = int(video.get("gop", 15))
    if nvenc_available():
        args = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-profile:v", "high", "-rc", "vbr",
                "-b:v", f"{kbps}k", "-maxrate", f"{top}k", "-bufsize", f"{2 * top}k", "-g", str(gop), "-bf", "2",
                "-spatial-aq", "1", "-temporal-aq", "1", "-rc-lookahead", "20"]
    else:
        args = ["-c:v", "libx264", "-preset", "faster", "-profile:v", "high", "-b:v", f"{kbps}k",
                "-maxrate", f"{top}k", "-bufsize", f"{2 * top}k",
                "-x264-params", f"keyint={gop}:min-keyint={gop}:scenecut=0:open-gop=0"]
    return args + ["-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709",
                   "-colorspace", "bt709", "-color_range", "tv"]


def _parts(layer: render.Layer) -> list[tuple[float, float, float, float]]:
    """(offset into the layer, length, fade in, fade out) pieces: fades kept apart from a long plain middle."""
    fi, fo, n = layer.fade_in, layer.fade_out, layer.length
    if (fi <= 0 and fo <= 0) or n - fi - fo < SPLIT_FADES_LONGER_THAN or layer.kind == "title":
        return [(0.0, n, fi, fo)]
    out = []
    if fi > 0:
        out.append((0.0, fi, fi, 0.0))
    out.append((fi, n - fi - fo, 0.0, 0.0))
    if fo > 0:
        out.append((n - fo, fo, 0.0, fo))
    return out


class Graph:
    """FFmpeg inputs and a filter graph, built layer by layer."""

    def __init__(self, total: float, width: int, height: int) -> None:
        self.total = total
        self.size = (width, height)
        # Kit videos are 1080p copies; another frame size (only in tests) scales them.
        self.fit = "" if self.size == kit.VIDEO_SIZE else f",scale={width}:{height}"
        self.inputs: list[list[str]] = []
        self.filters: list[str] = []
        base = self.add(["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={FPS}:d={total:.3f}"])
        self.filters.append(f"[{base}:v]format=yuv420p,setsar=1[v0]")
        self.current = "v0"
        self._n = 0

    def add(self, args: list[str]) -> int:
        self.inputs.append(args)
        return len(self.inputs) - 1

    def _label(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def overlay(self, chain: str, x: int = 0, y: int = 0) -> None:
        pic, out = self._label("l"), self._label("v")
        self.filters.append(f"{chain}[{pic}]")
        self.filters.append(f"[{self.current}][{pic}]overlay=x={x}:y={y}:eof_action=pass:format=yuv420[{out}]")
        self.current = out

    @staticmethod
    def _fades(at: float, length: float, fi: float, fo: float) -> str:
        out = ""
        if fi > 0:
            out += f",fade=t=in:st={at:.4f}:d={fi:.4f}:alpha=1"
        if fo > 0:
            out += f",fade=t=out:st={at + length - fo:.4f}:d={fo:.4f}:alpha=1"
        return out

    def video(self, src: Path, start: float, at: float, length: float, fi: float, fo: float) -> None:
        k = self.add(["-threads", "2", "-ss", f"{start:.4f}", "-t", f"{length + 0.5:.4f}", "-i", str(src)])
        chain = f"[{k}:v]trim=duration={length:.4f},setpts=PTS-STARTPTS+{at:.4f}/TB{self.fit}"
        if fi > 0 or fo > 0:
            chain += ",format=yuva420p" + self._fades(at, length, fi, fo)
        self.overlay(chain)

    def still(self, src: Path, start: float, at: float, length: float, fi: float, fo: float) -> None:
        k = self.add(["-threads", "2", "-ss", f"{start:.4f}", "-i", str(src)])
        chain = (f"[{k}:v]trim=end_frame=1,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={length:.4f},"
                 f"trim=duration={length:.4f},setpts=PTS-STARTPTS+{at:.4f}/TB{self.fit}")
        if fi > 0 or fo > 0:
            chain += ",format=yuva420p" + self._fades(at, length, fi, fo)
        self.overlay(chain)

    @staticmethod
    def _held(index: int, frames: int, at: float = 0.0) -> str:
        """A picture (an input opened with -framerate FPS, so one tick is one frame) turned into video
        colours once, then shown for `frames` frames from the frame nearest `at`, instead of decoding
        and converting the same picture again for every frame."""
        return (f"[{index}:v]format=rgba,scale=out_color_matrix=bt709:out_range=tv,format=yuva420p,"
                f"loop=loop={max(0, frames - 1)}:size=1:start=0,setpts=N+{round(at * FPS_F)}")

    def image(self, png: Path, x: int, y: int, at: float, length: float, fi: float, fo: float) -> None:
        if not LOOP_STILLS:
            k = self.add(["-loop", "1", "-framerate", FPS, "-t", f"{length:.4f}", "-i", str(png)])
            chain = (f"[{k}:v]format=rgba,setpts=PTS-STARTPTS+{at:.4f}/TB{self._fades(at, length, fi, fo)},"
                     "scale=out_color_matrix=bt709:out_range=tv,format=yuva420p")
            self.overlay(chain, x, y)
            return
        k = self.add(["-framerate", FPS, "-i", str(png)])
        self.overlay(self._held(k, math.ceil(length * FPS_F), at) + self._fades(at, length, fi, fo), x, y)

    def spectrum(self, box: spectrumlib.Box, fill_png: Path, rim_png: Path) -> None:
        w, h = box.width, box.height
        k = self.add(["-f", "rawvideo", "-pix_fmt", "gray", "-video_size", f"{w}x{2 * h}", "-framerate", FPS,
                      "-i", "pipe:0"])
        self.filters += [f"[{k}:v]split=2[sa][sb]", f"[sa]crop={w}:{h}:0:0[fa]", f"[sb]crop={w}:{h}:0:{h}[ra]"]
        if not LOOP_STILLS:
            f = self.add(["-loop", "1", "-framerate", FPS, "-t", f"{self.total:.3f}", "-i", str(fill_png)])
            r = self.add(["-loop", "1", "-framerate", FPS, "-t", f"{self.total:.3f}", "-i", str(rim_png)])
            for colour, alpha in ((f, "fa"), (r, "ra")):
                self.overlay(f"[{colour}:v]format=rgba[c{alpha}];[c{alpha}][{alpha}]alphamerge,"
                             "scale=out_color_matrix=bt709:out_range=tv,format=yuva420p", box.x, box.y)
            return
        frames = math.ceil(self.total * FPS_F) + 2
        for png, alpha in ((fill_png, "fa"), (rim_png, "ra")):
            colour = self.add(["-framerate", FPS, "-i", str(png)])
            self.overlay(f"{self._held(colour, frames)}[c{alpha}];[c{alpha}][{alpha}]alphamerge", box.x, box.y)

    def finish(self, fade_to_black: float) -> str:
        if fade_to_black > 0:
            self.filters.append(f"[{self.current}]fade=t=out:st={self.total - fade_to_black:.4f}:"
                                f"d={fade_to_black:.4f}[vout]")
        else:
            self.filters.append(f"[{self.current}]null[vout]")
        return ";".join(self.filters)


def needed(picture: render.Picture) -> dict[str, float]:
    """For each kit video the picture uses: how far into it the picture goes (seconds)."""
    out: dict[str, float] = {}
    for layer in picture.layers:
        if layer.kind in ("video", "still"):
            reach = layer.start + (layer.length if layer.kind == "video" else 0.0) + 1.0
            out[layer.src] = max(out.get(layer.src, 0.0), reach)
    return out


def render_video(plan: render.Plan, *, audio_path: Path | None, mix_path: Path, talk_lufs: float, dst: Path,
                 video: dict[str, Any], kit_video: KitVideo, title_lines: dict[str, str], work_dir: Path,
                 check: Callable[[], None] | None = None, progress: Callable[[float], None] | None = None,
                 faststart: bool = True) -> None:
    """Compose and encode the finished video (MP4) with the sound from `audio_path` copied in, or the
    picture alone when `audio_path` is None (the sound goes in afterwards with `mux`)."""
    picture = plan.picture
    if picture is None:
        raise ValueError("This plan has no picture.")
    total = plan.total
    width, height = int(video["width"]), int(video["height"])
    work_dir.mkdir(parents=True, exist_ok=True)
    graph = Graph(total, width, height)
    title_png = work_dir / "title.png"
    title_box = None
    if any(layer.kind == "title" for layer in picture.layers):
        title_box = titles.render(title_lines, title_png, width=width, height=height)
    for layer in picture.layers:
        for offset, length, fi, fo in _parts(layer):
            at = layer.at + offset
            if length <= 0.01:
                continue
            if layer.kind == "video":
                graph.video(kit_video(layer.src, layer.start + layer.length + 1.0), layer.start + offset, at, length, fi, fo)
            elif layer.kind == "still":
                graph.still(kit_video(layer.src, layer.start + 1.0), layer.start, at, length, fi, fo)
            elif layer.kind == "title" and title_box:
                x, y, _, _ = title_box
                graph.image(title_png, x, y, at, length, fi, fo)
    feed = None
    if picture.spectrum:
        drawer = spectrumlib.Spectrum(frame_height=height)
        fill_png, rim_png = work_dir / "spectrum-fill.png", work_dir / "spectrum-rim.png"
        _save_rgb(drawer.fill_colours(), fill_png)
        _save_rgb(drawer.rim_colours(), rim_png)
        graph.spectrum(drawer.box, fill_png, rim_png)
        sound = spectrumlib.mono_reader(mix_path, channels=2, gain_db=spectrumlib.TALK_LUFS - talk_lufs)
        visible = spectrumlib.windows_visibility(picture.spectrum)
        frames = math.ceil(total * FPS_F) + 2

        def feed(pipe: IO[bytes]) -> None:
            for chunk in drawer.frames(sound, frames, visible, check=check):
                pipe.write(chunk)

    audio = graph.add(["-i", str(audio_path)]) if audio_path else None
    script = graph.finish(picture.fade_to_black)
    inputs = [arg for group in graph.inputs for arg in group]
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    args = [*inputs, "-filter_complex", script, "-map", "[vout]"]
    if audio is not None:
        args += ["-map", f"{audio}:a", *_sound_args(audio)]
    else:
        args += ["-an"]
    args += [*encoder_args(video), "-t", f"{total:.3f}", *(["-movflags", "+faststart"] if faststart else []),
             "-f", "mp4", str(tmp)]
    try:
        ffmpeg.run(args, duration=total, progress=progress, check=check, feed=feed)
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)


def _sound_args(index: int) -> list[str]:
    # The sound is copied as it is. Its tags (title, show) come along; its chapters don't (YouTube reads
    # chapters from the description, and a chapter track only confuses some players).
    return ["-c:a", "copy", "-map_metadata", str(index), "-map_chapters", "-1"]


def mux(picture: Path, sound: Path, dst: Path, *, total: float, check: Callable[[], None] | None = None,
        progress: Callable[[float], None] | None = None) -> None:
    """The finished video: the picture with the sound put in, both copied as they are, index at the
    front. (The sound is encoded while the picture is drawn, see Cutzo._export_video.)"""
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    args = ["-i", str(picture), "-i", str(sound), "-map", "0:v", "-map", "1:a", "-c:v", "copy", *_sound_args(1),
            "-t", f"{total:.3f}", "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    try:
        ffmpeg.run(args, duration=total, progress=progress, check=check)
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)


def _save_rgb(rgb: np.ndarray, path: Path) -> None:
    from PIL import Image

    Image.fromarray(np.ascontiguousarray(rgb), "RGB").save(path)


# ------------------------------------------------------------------ checks
def check_video(path: Path, spec: dict[str, Any], *, audio_loudness: dict[str, float], expected_seconds: float,
                check: Callable[[], None] | None = None) -> dict[str, Any]:
    """Measure a finished video against its spec. The loudness was measured on the sound before it was
    copied into the video (the same stream), so the video isn't read twice."""
    info = ffmpeg.probe(path)
    v, a = info.get("video") or {}, info.get("audio") or {}
    want = spec["video"]
    rules = []

    def rule(name: str, expected: str, found: str, ok: bool) -> None:
        rules.append({"rule": name, "expected": expected, "found": found, "pass": bool(ok)})

    rule("File type", "MP4, index at the front", f"{info.get('format', '?')}, "
         f"{'index at the front' if moov_first(path) else 'index at the end'}",
         "mp4" in str(info.get("format") or "") and moov_first(path))
    rule("Video", f"H.264 {want['profile']}, {want['width']} x {want['height']}",
         f"{(v.get('codec') or 'none').upper()} {v.get('profile') or ''}, {v.get('width')} x {v.get('height')}",
         v.get("codec") == want["codec"] and v.get("profile") == want["profile"]
         and v.get("width") == want["width"] and v.get("height") == want["height"])
    rule("Frame rate", f"{want['fps']:g} fps", f"{v.get('fps') or 0:g} fps", abs((v.get("fps") or 0) - want["fps"]) < 0.02)
    kbps = round((v.get("bit_rate") or 0) / 1000)
    rule("Video bitrate", f"{want['min_kbps'] / 1000:g}-{want['max_kbps'] / 1000:g} Mbps", f"{kbps / 1000:.1f} Mbps",
         want["min_kbps"] <= kbps <= want["max_kbps"])
    rule("Colour", "BT.709", str(v.get("color_primaries") or "not marked").upper().replace("BT709", "BT.709"),
         v.get("color_primaries") == "bt709")
    gop = keyframe_gap(path)
    rule("Keyframes", f"every {want['gop']} frames", f"every {gop} frames" if gop else "couldn't tell",
         gop is not None and gop <= want["gop"])
    rule("Audio", f"AAC, stereo, {spec['rate'] / 1000:g} kHz", f"{(a.get('codec') or 'none').upper()}, "
         f"{ {1: 'mono', 2: 'stereo'}.get(a.get('channels'), a.get('channels'))}, {(a.get('rate') or 0) / 1000:g} kHz",
         a.get("codec") == "aac" and a.get("channels") == 2 and a.get("rate") == spec["rate"])
    rule("Loudness", f"{spec['loudness']:g} LUFS (±{spec['loudness_tolerance']:g})",
         f"{audio_loudness['integrated']:.1f} LUFS",
         abs(audio_loudness["integrated"] - spec["loudness"]) <= spec["loudness_tolerance"])
    rule("Peaks", f"at or below {spec['true_peak_max']:g} dBTP", f"{audio_loudness['true_peak']:.1f} dBTP",
         audio_loudness["true_peak"] <= spec["true_peak_max"] + 0.05)
    length = info.get("duration") or 0.0
    rule("Length", f"{expected_seconds:.1f} s", f"{length:.1f} s", abs(length - expected_seconds) <= 1.0)
    size = path.stat().st_size
    if want.get("max_gb"):
        rule("Size", f"{want['max_gb']:g} GB or less", f"{size / 1e9:.1f} GB", size <= want["max_gb"] * 1e9)
    return {"passed": all(r["pass"] for r in rules), "rules": rules, "duration": length, "bytes": size,
            "loudness": audio_loudness}


def moov_first(path: Path) -> bool:
    """Whether the MP4's index (moov) comes before the media (mdat), so playback can start at once."""
    with open(path, "rb") as f:
        pos = 0
        for _ in range(64):
            f.seek(pos)
            head = f.read(16)
            if len(head) < 8:
                return False
            size = int.from_bytes(head[:4], "big")
            kind = head[4:8]
            if size == 1:
                size = int.from_bytes(head[8:16], "big")
            if kind == b"moov":
                return True
            if kind == b"mdat" or size < 8:
                return False
            pos += size
    return False


def keyframe_gap(path: Path, seconds: float = 30.0) -> int | None:
    """The largest gap between keyframes (in frames) in the first `seconds` of the video."""
    try:
        out = subprocess.run([str(require("ffprobe")), "-v", "error", "-select_streams", "v:0",
                              "-read_intervals", f"%+{seconds:g}", "-show_entries", "packet=flags",
                              "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=120,
                             **ffmpeg._quiet())
    except (OSError, subprocess.SubprocessError):
        return None
    flags = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    keys = [k for k, f in enumerate(flags) if f.startswith("K")]
    if len(keys) < 2:
        return None
    return max(b - a for a, b in zip(keys, keys[1:]))
