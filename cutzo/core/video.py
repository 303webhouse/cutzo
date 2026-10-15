"""Making the finished video for shows with a picture (The New Heretics).

The recipe (recipes.py) says what the frame shows over time: layers of kit videos, stills, the
episode title graphic, and when the spectrum shows. Here FFmpeg puts it together in one pass, in
RGB (planar, so blending is exact):

    a black 1080p canvas
      + each layer at its time (kit videos come from 1080p copies made once, see kit.video_copy):
        "normal" layers cover what's below and dissolve in and out; "screen" layers lighten it, as
        Premiere's Screen mode does, so Nick's flame videos on black add only their flames
      + the title graphic and the spectrum (Cutzo draws it frame by frame, spectrum.py, and feeds
        it to FFmpeg as it goes)
      + the fade to black at the end, then BT.709 video colours
    with the episode's sound put in afterwards (see Cutzo._export_video).

Long normal layers that fade are split into a fading start, a plain middle and a fading end, so
FFmpeg only blends see-through pictures where it has to. Still pictures are converted once and held.
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
FRAME_TB = "1001/30000"  # one tick = one frame

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
    """H.264 High at a set quality (x264's CRF, or NVENC's constant quality), never above the target's
    `max_kbps`; keyframes every `gop` frames (closed GOP), 2 B-frames, BT.709. YouTube asks for a
    variable bitrate with no limit (its 8 Mbps for 1080p is a recommendation): a mostly still picture
    needs far less, so a fixed average undershot anyway (Spooky Sode's came out at 4 Mbps)."""
    top = int(video["max_kbps"])
    quality = int(video.get("quality", 18))
    gop = int(video.get("gop", 15))
    if nvenc_available():
        args = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-profile:v", "high", "-rc", "vbr",
                "-cq", str(quality + 2), "-b:v", "0", "-maxrate", f"{top}k", "-bufsize", f"{2 * top}k",
                "-g", str(gop), "-bf", "2", "-spatial-aq", "1", "-temporal-aq", "1", "-rc-lookahead", "20"]
    else:
        args = ["-c:v", "libx264", "-preset", "faster", "-profile:v", "high", "-crf", str(quality),
                "-maxrate", f"{top}k", "-bufsize", f"{2 * top}k", "-bf", "2",
                "-x264-params", f"keyint={gop}:min-keyint={gop}:scenecut=0:open-gop=0"]
    return args + ["-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709",
                   "-colorspace", "bt709", "-color_range", "tv"]


# FFmpeg 9 takes the colour labels from the frames, not from -color_primaries and friends (found on
# Nick's PC, Oct 15: the file said BT.709 for the matrix only), so the last filter sets them.
BT709 = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"


def _parts(layer: render.Layer) -> list[tuple[float, float, float, float]]:
    """(offset into the layer, length, fade in, fade out) pieces: fades kept apart from a long plain middle.
    Screen layers stay whole: they blend anyway, and their fades are just a change of brightness."""
    fi, fo, n = layer.fade_in, layer.fade_out, layer.length
    if (fi <= 0 and fo <= 0) or n - fi - fo < SPLIT_FADES_LONGER_THAN or layer.kind == "title" \
            or layer.blend == "screen":
        return [(0.0, n, fi, fo)]
    out = []
    if fi > 0:
        out.append((0.0, fi, fi, 0.0))
    out.append((fi, n - fi - fo, 0.0, 0.0))
    if fo > 0:
        out.append((n - fo, fo, 0.0, fo))
    return out


class Graph:
    """FFmpeg inputs and a filter graph, built layer by layer, in planar RGB."""

    def __init__(self, total: float, width: int, height: int) -> None:
        self.total = total
        self.size = (width, height)
        # Kit videos are 1080p copies; another frame size (only in tests) scales them.
        self.fit = "" if self.size == kit.VIDEO_SIZE else f"{width}:{height}:"
        self.inputs: list[list[str]] = []
        self.filters: list[str] = []
        base = self.add(["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={FPS}:d={total:.3f}"])
        self.filters.append(f"[{base}:v]format=gbrp,setsar=1[v0]")
        self.current = "v0"
        self._n = 0

    def add(self, args: list[str]) -> int:
        self.inputs.append(args)
        return len(self.inputs) - 1

    def _label(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def _rgb(self, alpha: bool) -> str:
        """A kit video's frames (BT.709, TV range) to planar RGB, with a see-through channel when it fades."""
        return f"scale={self.fit}in_color_matrix=bt709:in_range=tv,format={'gbrap' if alpha else 'gbrp'}"

    def overlay(self, chain: str, x: int = 0, y: int = 0) -> None:
        pic, out = self._label("l"), self._label("v")
        self.filters.append(f"{chain}[{pic}]")
        self.filters.append(f"[{self.current}][{pic}]overlay=x={x}:y={y}:eof_action=pass:format=gbrp[{out}]")
        self.current = out

    def screen(self, chain: str) -> None:
        """Lighten the picture so far with this layer (black changes nothing). Before the layer starts and
        after it ends, the picture passes through."""
        pic, out = self._label("l"), self._label("v")
        self.filters.append(f"{chain}[{pic}]")
        self.filters.append(f"[{self.current}][{pic}]blend=all_mode=screen:eof_action=pass[{out}]")
        self.current = out

    @staticmethod
    def _fades(at: float, length: float, fi: float, fo: float, alpha: bool = True) -> str:
        """Fades: of the see-through channel (a dissolve), or to and from black (for screen layers)."""
        how = ":alpha=1" if alpha else ""
        out = ""
        if fi > 0:
            out += f",fade=t=in:st={at:.4f}:d={fi:.4f}{how}"
        if fo > 0:
            out += f",fade=t=out:st={at + length - fo:.4f}:d={fo:.4f}{how}"
        return out

    def _place(self, chain: str, at: float, length: float, fi: float, fo: float, blend: str) -> None:
        if blend == "screen":
            self.screen(chain + self._fades(at, length, fi, fo, alpha=False))
        else:
            self.overlay(chain + self._fades(at, length, fi, fo))

    def video(self, src: Path, start: float, at: float, length: float, fi: float, fo: float, *,
              blend: str = "normal", speed: float = 1.0) -> None:
        reach = length * speed
        k = self.add(["-threads", "2", "-ss", f"{start:.4f}", "-t", f"{reach + 0.5:.4f}", "-i", str(src)])
        chain = f"[{k}:v]trim=duration={reach:.4f},setpts=(PTS-STARTPTS)/{speed:.6f}+{at:.4f}/TB"
        if speed != 1.0:
            chain += f",fps={FPS}"  # faster than life: keep only the frames the video shows
        fading = blend == "normal" and (fi > 0 or fo > 0)
        self._place(f"{chain},{self._rgb(fading)}", at, length, fi, fo, blend)

    @staticmethod
    def _hold(frames: int, at: float) -> str:
        """Show one converted frame for `frames` frames from the frame nearest `at`."""
        return f"loop=loop={max(0, frames - 1)}:size=1:start=0,settb={FRAME_TB},setpts=N+{round(at * FPS_F)}"

    def still(self, src: Path, start: float, at: float, length: float, fi: float, fo: float, *,
              blend: str = "normal") -> None:
        k = self.add(["-threads", "2", "-ss", f"{start:.4f}", "-i", str(src)])
        fading = blend == "normal" and (fi > 0 or fo > 0)
        chain = f"[{k}:v]trim=end_frame=1,{self._rgb(fading)},{self._hold(math.ceil(length * FPS_F), at)}"
        self._place(chain, at, length, fi, fo, blend)

    def image(self, png: Path, x: int, y: int, at: float, length: float, fi: float, fo: float) -> None:
        """A see-through picture file (the title graphic), already in RGB."""
        k = self.add(["-framerate", FPS, "-i", str(png)])
        chain = f"[{k}:v]format=rgba,format=gbrap,{self._hold(math.ceil(length * FPS_F), at)}"
        self.overlay(chain + self._fades(at, length, fi, fo), x, y)

    def spectrum(self, box: spectrumlib.Box, fill_png: Path, rim_png: Path) -> None:
        w, h = box.width, box.height
        k = self.add(["-f", "rawvideo", "-pix_fmt", "gray", "-video_size", f"{w}x{2 * h}", "-framerate", FPS,
                      "-i", "pipe:0"])
        self.filters += [f"[{k}:v]split=2[sa][sb]", f"[sa]crop={w}:{h}:0:0[fa]", f"[sb]crop={w}:{h}:0:{h}[ra]"]
        frames = math.ceil(self.total * FPS_F) + 2
        for png, alpha in ((fill_png, "fa"), (rim_png, "ra")):
            colour = self.add(["-framerate", FPS, "-i", str(png)])
            self.overlay(f"[{colour}:v]format=rgb24,format=gbrap,{self._hold(frames, 0.0)}[c{alpha}];"
                         f"[c{alpha}][{alpha}]alphamerge", box.x, box.y)

    def finish(self, fade_to_black: float) -> str:
        fade = (f"fade=t=out:st={self.total - fade_to_black:.4f}:d={fade_to_black:.4f},"
                if fade_to_black > 0 else "")
        self.filters.append(f"[{self.current}]{fade}scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                            f"{BT709}[vout]")
        return ";".join(self.filters)


def needed(picture: render.Picture) -> dict[str, float]:
    """For each kit video the picture uses: how far into it the picture goes (seconds)."""
    out: dict[str, float] = {}
    for layer in picture.layers:
        if layer.kind in ("video", "still"):
            out[layer.src] = max(out.get(layer.src, 0.0), layer.reach + 1.0)
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
                graph.video(kit_video(layer.src, layer.reach + 1.0), layer.start + offset * layer.speed, at, length,
                            fi, fo, blend=layer.blend, speed=layer.speed)
            elif layer.kind == "still":
                graph.still(kit_video(layer.src, layer.start + 1.0), layer.start, at, length, fi, fo, blend=layer.blend)
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
    rule("Video bitrate", f"up to {want['max_kbps'] / 1000:g} Mbps", f"{kbps / 1000:.1f} Mbps",
         want["min_kbps"] <= kbps <= want["max_kbps"])
    labels = [v.get("color_primaries"), v.get("color_transfer"), v.get("color_space")]
    rule("Colour", "BT.709 (primaries, transfer, matrix)",
         "BT.709" if labels == ["bt709"] * 3 else " / ".join(str(x or "not marked") for x in labels),
         labels == ["bt709"] * 3)
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
