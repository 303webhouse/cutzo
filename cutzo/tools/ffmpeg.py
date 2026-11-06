"""FFmpeg and ffprobe: the only place Cutzo builds their command lines.

Cutzo's working audio is raw 32-bit float samples (".f32" files, no header) at
48 kHz, so other parts of Cutzo can memory-map them. These helpers convert to and
from that format, measure loudness and encode the finished files.
"""

from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import IO, Any, Callable, Iterable

from ..core.errors import CutzoError
from . import require

RATE = 48000
CREATE_NO_WINDOW = 0x08000000

Progress = Callable[[float], None]
Check = Callable[[], None]


def _quiet() -> dict[str, Any]:
    return {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}


def version() -> str | None:
    try:
        out = subprocess.run([str(require("ffmpeg")), "-hide_banner", "-version"], capture_output=True, text=True,
                             timeout=30, **_quiet()).stdout
    except (OSError, subprocess.SubprocessError, CutzoError):
        return None
    m = re.match(r"ffmpeg version (\S+)", out)
    return m.group(1) if m else None


# --------------------------------------------------------------------- probe
def _fraction(text: str | None) -> float | None:
    if not text or text in ("0/0", "N/A"):
        return None
    if "/" in text:
        a, b = text.split("/", 1)
        try:
            return float(a) / float(b) if float(b) else None
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def probe(path: str | Path) -> dict[str, Any]:
    """What a media file contains: length, audio and video details. Read-only."""
    path = Path(path)
    cmd = [str(require("ffprobe")), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=300, **_quiet())
    except subprocess.TimeoutExpired:
        raise CutzoError(f"Reading {path.name} took too long. If it's in Google Drive, make it available offline first.") from None
    if proc.returncode != 0:
        reason = (proc.stderr.decode("utf-8", "replace").strip().splitlines() or ["unknown problem"])[-1]
        raise CutzoError(f"Couldn't read {path.name}: {reason}")
    data = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    out: dict[str, Any] = {
        "duration": _fraction(fmt.get("duration")),
        "format": fmt.get("format_name"),
        "bit_rate": _int(fmt.get("bit_rate")),
        "size": _int(fmt.get("size")),
        "tags": fmt.get("tags", {}),
        "audio": None,
        "video": None,
    }
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio:
        out["audio"] = {
            "codec": audio.get("codec_name"),
            "rate": _int(audio.get("sample_rate")),
            "channels": _int(audio.get("channels")),
            "layout": audio.get("channel_layout"),
            "bit_rate": _int(audio.get("bit_rate")),
            "sample_format": audio.get("sample_fmt"),
            "duration": _fraction(audio.get("duration")),
        }
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video:
        avg, real = _fraction(video.get("avg_frame_rate")), _fraction(video.get("r_frame_rate"))
        out["video"] = {
            "codec": video.get("codec_name"),
            "profile": video.get("profile"),
            "width": _int(video.get("width")),
            "height": _int(video.get("height")),
            "fps": round(avg, 3) if avg else None,
            "variable_frame_rate": bool(avg and real and abs(avg - real) > 0.01),
            "pixel_format": video.get("pix_fmt"),
            "color_transfer": video.get("color_transfer"),
            "color_primaries": video.get("color_primaries"),
            "color_space": video.get("color_space"),
            "bit_rate": _int(video.get("bit_rate")),
        }
    return out


# ----------------------------------------------------------------------- run
def run(args: Iterable[str], *, duration: float | None = None, progress: Progress | None = None,
        check: Check | None = None, keep_lines: int = 60,
        feed: Callable[[IO[bytes]], None] | None = None) -> list[str]:
    """Run ffmpeg with progress reports. `check` is called often and may raise to stop it.
    `feed`, if given, writes what FFmpeg reads as "pipe:0" (in its own thread).
    Returns the last lines FFmpeg printed (where measurements such as loudness appear)."""
    cmd = [str(require("ffmpeg")), "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", *map(str, args)]
    if feed is None:
        cmd.insert(2, "-nostdin")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if feed else subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **_quiet())
    tail: collections.deque[str] = collections.deque(maxlen=keep_lines)
    fed: list[BaseException] = []

    def drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            tail.append(line.rstrip())

    def write() -> None:
        assert proc.stdin is not None and feed is not None
        try:
            feed(proc.stdin.buffer)
        except (BrokenPipeError, ConnectionResetError, ValueError):
            pass  # FFmpeg stopped reading (finished early or failed): its own error says why
        except BaseException as exc:  # noqa: BLE001 - reported below, after FFmpeg is stopped
            fed.append(exc)
            proc.kill()
        finally:
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    writer = threading.Thread(target=write, daemon=True) if feed else None
    if writer:
        writer.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if check:
                check()
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and duration and progress:
                try:
                    progress(max(0.0, min(1.0, int(value) / 1e6 / duration)))
                except ValueError:
                    pass
        proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        reader.join(timeout=10)
        if writer:
            writer.join(timeout=10)
    if fed:
        raise fed[0]
    if proc.returncode != 0:
        lines = [x for x in tail if x.strip()]
        reason = lines[-1] if lines else f"exit code {proc.returncode}"
        raise CutzoError(f"FFmpeg couldn't finish: {reason}", data={"ffmpeg": lines[-12:]})
    if progress:
        progress(1.0)
    return list(tail)


def raw_input_args(path: Path, channels: int = 1, rate: int = RATE) -> list[str]:
    """How to hand one of Cutzo's .f32 working files to FFmpeg."""
    return ["-f", "f32le", "-ar", str(rate), "-ac", str(channels), "-i", str(path)]


def decode(src: Path, dst: Path, *, channels: int = 1, rate: int = RATE, start: float | None = None,
           length: float | None = None, filters: str | None = None, duration: float | None = None,
           progress: Progress | None = None, check: Check | None = None) -> Path:
    """Any audio or video file -> Cutzo working file (.f32), or a .wav when dst ends in .wav."""
    args: list[str] = []
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(src)]
    if length:
        args += ["-t", f"{length:.3f}"]
    args += ["-vn", "-sn", "-dn", "-ac", str(channels), "-ar", str(rate)]
    if filters:
        args += ["-af", filters]
    if dst.suffix.lower() == ".wav":
        args += ["-c:a", "pcm_f32le", "-f", "wav", str(dst)]
    else:
        args += ["-c:a", "pcm_f32le", "-f", "f32le", str(dst)]
    run(args, duration=length or duration, progress=progress, check=check)
    return dst


def filter_raw(src: Path, dst: Path, filters: str, *, channels: int = 1, out_channels: int | None = None,
               duration: float | None = None, progress: Progress | None = None, check: Check | None = None) -> Path:
    """Run an FFmpeg audio filter chain over a working file (or a .wav) into another one."""
    args = raw_input_args(src, channels) if src.suffix.lower() == ".f32" else ["-i", str(src)]
    args += ["-af", filters, "-ac", str(out_channels or channels), "-ar", str(RATE), "-c:a", "pcm_f32le"]
    args += ["-f", "wav" if dst.suffix.lower() == ".wav" else "f32le", str(dst)]
    run(args, duration=duration, progress=progress, check=check)
    return dst


# ------------------------------------------------------------------ loudness
_SUMMARY = {
    "integrated": re.compile(r"^\s*I:\s+(-?[\d.]+|-inf) LUFS", re.M),
    "threshold": re.compile(r"^\s*Threshold:\s+(-?[\d.]+|-inf) LUFS", re.M),
    "range": re.compile(r"^\s*LRA:\s+(-?[\d.]+|-inf) LU", re.M),
    "true_peak": re.compile(r"^\s*Peak:\s+(-?[\d.]+|-inf) dBFS", re.M),
}


def loudness(src: Path, *, channels: int = 1, duration: float | None = None, progress: Progress | None = None,
             check: Check | None = None, filters: str | None = None, peak: bool = True) -> dict[str, float]:
    """EBU R128 measurement: integrated loudness (LUFS), true peak (dBTP), loudness range (LU).
    `filters` run first (to measure what a gain and limiter would make, without encoding anything);
    peak=False skips the true peak, which is slower to find.

    Uses FFmpeg's ebur128 meter, which reads the summary to one decimal and runs about seven times
    faster than loudnorm's (measured on a 90-minute episode: 20 s against 137 s, same results)."""
    args = raw_input_args(src, channels) if src.suffix.lower() == ".f32" else ["-i", str(src)]
    meter = f"ebur128=peak={'true' if peak else 'none'}:framelog=quiet"
    args += ["-vn", "-af", f"{filters},{meter}" if filters else meter, "-f", "null", "-"]
    lines = run(args, duration=duration, progress=progress, check=check, keep_lines=40)
    text = "\n".join(lines)
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else ""
    out: dict[str, float] = {}
    for key, pattern in _SUMMARY.items():
        if key == "true_peak" and not peak:
            continue
        found = pattern.search(summary)
        if not found:
            raise CutzoError("FFmpeg didn't report the loudness.", data={"ffmpeg": lines[-12:]})
        value = found.group(1)
        out[key] = -120.0 if value == "-inf" else max(-120.0, round(float(value), 2))  # silence: keep it plain JSON
    return out


# ------------------------------------------------------------------ metadata
def ffmetadata(tags: dict[str, str], chapters: list[dict[str, Any]], total: float) -> str:
    """An FFmpeg metadata file: tags plus chapters (MP3 files get ID3 chapter frames)."""

    def esc(text: str) -> str:
        return re.sub(r"([=;#\\\n])", r"\\\1", str(text))

    lines = [";FFMETADATA1"]
    lines += [f"{k}={esc(v)}" for k, v in tags.items() if v]
    starts = [c["time"] for c in chapters]
    for n, chapter in enumerate(chapters):
        start = int(round(chapter["time"] * 1000))
        end = int(round((starts[n + 1] if n + 1 < len(starts) else total) * 1000))
        if end <= start:
            continue
        lines += ["", "[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}", f"title={esc(chapter['title'])}"]
    return "\n".join(lines) + "\n"
