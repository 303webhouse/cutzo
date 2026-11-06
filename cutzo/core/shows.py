"""Show templates: what every episode of a show gets.

A template lists "slots" (opening montage, background loop, music bed...).
Each slot has a default kit asset and the choices it can be swapped for.
Episodes can override any slot (see Cutzo.swap_asset).
"""

from __future__ import annotations

import copy
from typing import Any

from . import kit

_BACKGROUNDS = ["tnh-bg-loop-1hr", "tnh-fire-loop-1min", "tnh-fire-loop", "tnh-bg-master",
                "tnh-full-flames", "tnh-streaking-fire"]
_DIG_SFX = ["dig-sfx-mining-chops", "dig-sfx-three-digs", "dig-sfx-twolves-win", "dig-sfx-national-anthem",
            "dig-sfx-tom-haiku-intro", "dig-sfx-untitled"]

SHOWS: list[dict[str, Any]] = [
    {
        "id": "the-dig",
        "name": "The Dig",
        "network": "Denver Stiffs",
        "format": "audio",
        "summary": "Audio podcast for Substack and the RSS feed.",
        "phase": 2,
        "phase_note": "Phase 2 rebuilds Season Debrief Part 2 and compares it with your finished MP3.",
        "test_episode": "the-dig-season-debrief-part-2",
        "color": "#d9a441",
        "outputs": ["podcast-mp3"],
        "voice": {"preset": "Podcast voice", "strength": 25},  # light: Nick's pick (Oct 14, 2026)
        "loudness": {"podcast": -16, "video": -14},
        # Episode layout copied from Season Debrief Part 2 in Premiere (see recipes.py).
        "recipe": "the-dig",
        "slots": [
            {"slot": "theme", "label": "Theme music", "default": "dig-theme", "choices": ["dig-theme", "none"],
             "note": "Under the cold open, between segments and at the end, like Season Debrief Part 2."},
            {"slot": "montage", "label": "Intro sound bites", "default": "dig-montage", "choices": ["dig-montage", "none"],
             "note": "Plays after the cold open, before the conversation starts."},
            {"slot": "stinger", "label": "Segment stinger", "default": "dig-sfx-mining-chops",
             "choices": _DIG_SFX + ["none"], "note": "Plays right before each new segment (each chapter after the first)."},
            {"slot": "cover", "label": "Cover art", "default": "dig-jok-banner", "choices": ["dig-jok-banner"],
             "note": "Used if you ever post an episode to YouTube with a still image."},
        ],
    },
    {
        "id": "the-new-heretics",
        "name": "The New Heretics",
        "network": None,
        "format": "video",
        "summary": "Video podcast for YouTube (fire background, spectrum, opening montage), plus audio for Substack and RSS.",
        "phase": 3,
        "phase_note": "Phase 3 rebuilds Spooky Sode and checks it against JW Ghost Stories.mp4.",
        "test_episode": "the-new-heretics-spooky-sode",
        "color": "#ff6a3d",
        "outputs": ["youtube-1080p", "podcast-mp3"],  # 1080p: Nick's pick (Oct 15, 2026)
        "voice": {"preset": "Podcast voice", "strength": 25},  # light: Nick's pick (Oct 14, 2026)
        "loudness": {"podcast": -16, "video": -14},
        # Episode layout copied from Spooky Sode in Premiere (see recipes.py).
        "recipe": "the-new-heretics",
        "breaks_at": "segments",  # music breaks only before chapters marked as a new segment
        "slots": [
            {"slot": "montage", "label": "Opening montage", "default": "tnh-montage",
             "choices": ["tnh-montage", "tnh-intro", "none"],
             "note": "Plays after the teaser with its own intro music; the teaser ends as the music starts."},
            {"slot": "background", "label": "Background loop", "default": "tnh-bg-loop-1hr", "choices": _BACKGROUNDS,
             "note": "Runs behind the conversation."},
            {"slot": "spectrum", "label": "Audio spectrum", "default": "spectrum-ghost-bars",
             "choices": ["spectrum-ghost-bars", "none"], "note": "Moves with the voices, like your After Effects spectrum."},
            {"slot": "break_music", "label": "Break music", "default": "tnh-cues-theocretus",
             "choices": ["tnh-cues-theocretus", "tnh-cue-239-short3", "tnh-cue-239-short2", "tnh-cue-239-short1",
                         "tnh-cue-93696", "tnh-cue-god-of-love", "none"],
             "note": "Fades in under the end of a segment, plays on its own, then the next segment starts."},
            {"slot": "break_visuals", "label": "Break visuals", "default": "tnh-music-loop",
             "choices": ["tnh-music-loop", "none"], "note": "Shown during music breaks and the ending."},
            {"slot": "ending_music", "label": "Ending music", "default": "tnh-cue-239-full",
             "choices": ["tnh-cue-239-full", "tnh-cue-239-instrumental", "tnh-cue-god-of-love", "none"],
             "note": "Fades in under the last minute of talk and stays, quietly, under the sign-off."},
            {"slot": "ending_visuals", "label": "Ending visuals", "default": "tnh-full-flames",
             "choices": ["tnh-full-flames", "none"], "note": "After the talk and under the sign-off; fades to black."},
            {"slot": "transition", "label": "Transition sound", "default": "tnh-page-flip",
             "choices": ["tnh-page-flip", "tnh-turn-the-page", "none"],
             "note": "Plays at each new topic (chapter) and just before the talk comes back after a break."},
            {"slot": "art", "label": "Podcast art", "default": "tnh-podcast-art", "choices": ["tnh-podcast-art"],
             "note": "Shown in the audio version's player."},
        ],
        # Chapters whose title matches one of these play the voiced title first.
        "segment_titles": ["tnh-seg-the-new-heretics", "tnh-seg-define-your-terms", "tnh-seg-why-should-we-care",
                           "tnh-seg-building-a-better-apostasy", "tnh-seg-heretical-haiku", "tnh-seg-logo-loses-us"],
    },
]

_BY_ID = {s["id"]: s for s in SHOWS}


def list_shows() -> list[dict[str, Any]]:
    return [copy.deepcopy(s) for s in SHOWS]


def get_show(show_id: str) -> dict[str, Any] | None:
    show = _BY_ID.get(show_id)
    return copy.deepcopy(show) if show else None


def find_show(query: str) -> dict[str, Any] | None:
    """Match by id, name, or a unique part of the name ("dig", "heretics")."""
    q = (query or "").strip().lower()
    if not q:
        return None
    for show in SHOWS:
        if q in (show["id"], show["name"].lower()):
            return copy.deepcopy(show)
    hits = [s for s in SHOWS if q in s["name"].lower() or q in s["id"]]
    return copy.deepcopy(hits[0]) if len(hits) == 1 else None


def slot_def(show: dict[str, Any], slot: str) -> dict[str, Any] | None:
    for s in show.get("slots", []):
        if s["slot"] == slot:
            return s
    return None


def segment_title_for(show: dict[str, Any], chapter_title: str) -> dict[str, Any] | None:
    """The voiced title clip whose name matches a chapter title, if any."""
    wanted = _norm(chapter_title)
    for asset_id in show.get("segment_titles", []):
        asset = kit.get_asset(asset_id)
        if asset and _norm(asset["name"].rsplit(".", 1)[0]) == wanted:
            return asset
    return None


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())
