"""Recognising recordings by their file names (read-only; nothing is changed).

Handles the sources from the plan:
  - StreamYard individual tracks, e.g.
    TNH_Episode_1-Nick_Baren-webcam-00h_00m_00s_571ms-StreamYard.wav
    (the start offset in the name is the first sync guess)
  - StreamYard combined recordings (one MP3 or MP4 with everyone mixed)
  - Panasonic GH5 clips (P1000123.MOV / .MP4), split at size limits
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

_SY_OFFSET = re.compile(r"(\d+)h_(\d+)m_(\d+)s_(\d+)ms", re.IGNORECASE)
_SY_TRACK = re.compile(
    r"^(?P<recording>.+?)-(?P<person>[^-]+)-(?P<source>webcam|screen|camera|mic|microphone|audio)"
    r"-\d+h_\d+m_\d+s_\d+ms-StreamYard",
    re.IGNORECASE,
)
_GH5 = re.compile(r"^P\d{7}\.(mov|mp4)$", re.IGNORECASE)


def kind_of(path: str | Path) -> str | None:
    ext = Path(path).suffix.lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    return None


def streamyard_offset(name: str) -> float | None:
    """Seconds encoded in a StreamYard track name, or None."""
    m = _SY_OFFSET.search(name)
    if not m:
        return None
    h, mi, s, ms = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000.0


def classify(path: str | Path) -> dict[str, Any]:
    """Describe one file: kind, where it came from, who is on it, first sync guess."""
    p = Path(path)
    name = re.split(r"[\\/]", str(path))[-1] or p.name
    info: dict[str, Any] = {
        "path": str(p),
        "name": name,
        "kind": kind_of(p),
        "source": "other",
        "source_label": "Recording",
        "person": None,
        "offset": None,
    }
    track = _SY_TRACK.match(name)
    if track:
        info.update(
            source="streamyard-track",
            source_label="StreamYard track (one person)",
            person=track.group("person").replace("_", " ").strip(),
            offset=streamyard_offset(name),
        )
    elif "streamyard" in name.lower():
        info.update(source="streamyard-mix", source_label="StreamYard recording (everyone mixed)")
    elif _GH5.match(name):
        info.update(source="gh5", source_label="GH5 camera clip")
    elif info["kind"] == "audio" and p.suffix.lower() == ".mp3":
        info.update(source="mix", source_label="Combined recording (everyone mixed)")
    return info


def scan_folder(folder: str | Path, *, max_depth: int = 2, limit: int = 500) -> list[dict[str, Any]]:
    """List recordings in a folder (and up to two folders down). Read-only."""
    root = Path(folder)
    found: list[dict[str, Any]] = []
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - base_depth
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and depth < max_depth)
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            kind = kind_of(fname)
            if kind not in ("audio", "video"):
                continue
            info = classify(Path(dirpath) / fname)
            try:
                info["bytes"] = (Path(dirpath) / fname).stat().st_size
            except OSError:
                info["bytes"] = None
            found.append(info)
            if len(found) >= limit:
                return found
    return found
