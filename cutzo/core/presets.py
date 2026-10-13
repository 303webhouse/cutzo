"""Export targets and the rules each finished file is checked against.

Specs from the plan doc's Build Notes (YouTube, Substack and Apple pages,
checked Oct 12-13, 2026). Phase 1 shows the rules; from Phase 2 the check runs
ffprobe and a loudness scan on the real file.
"""

from __future__ import annotations

import copy
from typing import Any

PRESETS: list[dict[str, Any]] = [
    {
        "id": "youtube-4k",
        "label": "YouTube 4K",
        "kind": "video",
        "ext": "mp4",
        "summary": "Sharpest YouTube upload. Big file: about 16-20 GB per hour.",
        "gb_per_hour": 18.0,
        "rules": [
            ("File type", "MP4 with the index at the front (fast start)"),
            ("Video", "H.264 High, 3840 x 2160, same frame rate as recorded"),
            ("Bitrate", "35-45 Mbps (24-30 fps), 53-68 Mbps (48-60 fps)"),
            ("Keyframes", "Closed GOP, half the frame rate"),
            ("Color", "BT.709"),
            ("Audio", "AAC, stereo, 48 kHz, 384 kbps"),
            ("Loudness", "-14 LUFS, peaks at or below -1 dBTP"),
        ],
    },
    {
        "id": "youtube-1080p",
        "label": "YouTube 1080p",
        "kind": "video",
        "ext": "mp4",
        "summary": "Faster upload, about 4 GB per hour.",
        "gb_per_hour": 4.0,
        "rules": [
            ("File type", "MP4 with the index at the front (fast start)"),
            ("Video", "H.264 High, 1920 x 1080, same frame rate as recorded"),
            ("Bitrate", "8 Mbps (24-30 fps), 12 Mbps (48-60 fps)"),
            ("Keyframes", "Closed GOP, half the frame rate"),
            ("Color", "BT.709"),
            ("Audio", "AAC, stereo, 48 kHz, 384 kbps"),
            ("Loudness", "-14 LUFS, peaks at or below -1 dBTP"),
        ],
    },
    {
        "id": "substack-video",
        "label": "Substack video",
        "kind": "video",
        "ext": "mp4",
        "summary": "For a Substack video post: 1080p, 8 Mbps or less, under 20 GB.",
        "gb_per_hour": 3.6,
        "rules": [
            ("File type", "MP4"),
            ("Video", "H.264, 1920 x 1080 or smaller"),
            ("Bitrate", "8 Mbps or less"),
            ("Frame rate", "5-120 fps"),
            ("Audio", "AAC"),
            ("Size", "20 GB or less"),
        ],
    },
    {
        "id": "podcast-mp3",
        "label": "Podcast MP3 (Substack + RSS)",
        "kind": "audio",
        "ext": "mp3",
        "summary": "The audio file for Substack and the RSS feed.",
        "gb_per_hour": 0.06,
        "rules": [
            ("File type", "MP3"),
            ("Audio", "Stereo, 44.1 kHz, 128 kbps (matched to your current files in Phase 2)"),
            ("Loudness", "-16 LUFS (plus or minus 1), peaks at or below -1 dBTP"),
        ],
    },
    {
        "id": "podcast-aac",
        "label": "Podcast AAC (Apple's preference)",
        "kind": "audio",
        "ext": "m4a",
        "summary": "Same audio as AAC, which Apple Podcasts prefers over MP3.",
        "gb_per_hour": 0.09,
        "rules": [
            ("File type", "M4A (AAC)"),
            ("Audio", "Stereo, 44.1 or 48 kHz, 128-256 kbps"),
            ("Loudness", "-16 LUFS (plus or minus 1), peaks at or below -1 dBTP"),
        ],
    },
    {
        "id": "spotify-video",
        "label": "Spotify video (kept in reserve)",
        "kind": "video",
        "ext": "mp4",
        "reserve": True,
        "summary": "Not a current target. 1080p at 25 Mbps or 4K at 35 Mbps, steady bitrate.",
        "gb_per_hour": 11.0,
        "rules": [
            ("File type", "MP4, one video and one audio track"),
            ("Video", "H.264 or H.265, 1080p at 25 Mbps or 4K at 35 Mbps, constant bitrate"),
            ("Keyframes", "About every second"),
        ],
    },
]

_BY_ID = {p["id"]: p for p in PRESETS}


def list_presets(include_reserve: bool = False) -> list[dict[str, Any]]:
    return [_public(p) for p in PRESETS if include_reserve or not p.get("reserve")]


def get_preset(preset_id: str) -> dict[str, Any] | None:
    p = _BY_ID.get(preset_id)
    return _public(p) if p else None


def find_preset(query: str) -> dict[str, Any] | None:
    q = (query or "").strip().lower().replace(" ", "-").replace("_", "-")
    if q in _BY_ID:
        return _public(_BY_ID[q])
    aliases = {
        "youtube": "youtube-4k", "yt": "youtube-4k", "4k": "youtube-4k", "1080": "youtube-1080p",
        "1080p": "youtube-1080p", "youtube-1080": "youtube-1080p", "substack": "podcast-mp3",
        "substack-audio": "podcast-mp3", "rss": "podcast-mp3", "mp3": "podcast-mp3", "audio": "podcast-mp3",
        "podcast": "podcast-mp3", "apple": "podcast-aac", "aac": "podcast-aac", "m4a": "podcast-aac",
        "spotify": "spotify-video",
    }
    if q in aliases:
        return _public(_BY_ID[aliases[q]])
    hits = [p for p in PRESETS if q and (q in p["id"] or q in p["label"].lower().replace(" ", "-"))]
    return _public(hits[0]) if len(hits) == 1 else None


def _public(p: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(p)
    out["rules"] = [{"rule": r, "expected": e} for r, e in p["rules"]]
    out.setdefault("reserve", False)
    return out
