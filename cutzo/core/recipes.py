"""Show recipes: where each show's kit goes in a finished episode.

The Dig's recipe copies Nick's own edit of Season Debrief Part 2 in Premiere (June 2026),
read from its project file:

    Intro    the theme starts at 0:00 under a cold open (highlights Nick picks), then a montage of
             sound bites, and the conversation starts about a minute in while the theme fades out.
    Breaks   before each segment after the first, the theme fades in under the last seconds of
             talk, plays on its own, fades out, then the stinger, then the next segment.
    Ending   the theme fades in under the last seconds of talk, then the sign-off clips Nick picks,
             then the theme fades away.

Levels are Premiere's clip gains, measured against the talking in Nick's finished file
(-19.2 LUFS there), so Cutzo keeps his balance between talk, theme and bites. The finished
file is then set to the podcast loudness target as a whole.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import render
from .errors import CutzoError

TALK_REFERENCE_LUFS = -19.2

THE_DIG: dict[str, Any] = {
    "intro": {
        "cold_open_at": 0.5,
        "cold_open_gain_db": 4.0,
        "cold_open_end": 19.15,  # where Nick's cold open ended; everything after moves with it
        "theme": {"start": 0.0, "length": 64.531, "at": 0.2, "gain_db": -4.4, "fade_out": 12.8},
        "bites": [  # asset, start, end (in the asset), at (finished time), Premiere gain
            ("dig-intro-not-meant", 10.777, 15.382, 23.023, 8.6),
            ("dig-intro-doug-moe", 17.084, 18.685, 29.429, 9.0),
            ("dig-intro-doug-moe", 23.39, 25.225, 31.031, 9.4),
            ("dig-intro-doug-moe", 27.094, 29.062, 32.866, 9.9),
            ("dig-intro-take-that-l", 1.468, 3.103, 37.337, 9.0),
            ("dig-intro-take-that-l", 3.804, 5.906, 38.972, 8.2),
            ("dig-intro-audio", 11.979, 16.016, 46.78, 0.0),
        ],
        "talk_at": 60.661,
    },
    "break": {
        "theme_starts": [0.0, 83.45],  # alternate between two parts of the song
        "lead_in": 15.0,  # seconds of theme fading in under the end of a segment
        "alone": 27.0,  # seconds of theme on its own after the talking stops
        "theme_gain_db": -5.3,
        "fade_out": 3.0,
        "stinger_length": 1.1,
        "stinger_gain_db": 5.1,
    },
    "outro": {
        "lead_in": 18.0,
        "theme_gain_db": 0.45,
        "clips_after": 3.0,  # sign-off clips start this long after the talking ends
        "clips_gain_db": 6.5,
        "tail": 7.2,  # theme keeps playing this long after the last clip
        "fade_out": 10.0,
        "no_clips_tail": 20.0,
    },
    "max_cold_open": 45.0,
    "max_outro": 60.0,
}

RECIPES = {"the-dig": THE_DIG}


def slot_choice(show: dict[str, Any], ep: dict[str, Any], slot: str) -> str:
    for s in show.get("slots", []):
        if s["slot"] == slot:
            return (ep.get("assets") or {}).get(slot, s["default"])
    return "none"


def plan_episode(ep: dict[str, Any], show: dict[str, Any], *, voice_path: Path, talk_lufs: float,
                 kit_file: Callable[[str], Path], length_of: Callable[[str], float]) -> render.Plan:
    """Lay out the finished episode: intro, the conversation with breaks, and the ending."""
    recipe = RECIPES.get(show.get("recipe") or "")
    duration = float(ep["duration"])
    kept = render.kept_ranges(duration, ep.get("edits", []))
    if not kept:
        raise CutzoError("Everything is cut, so there's nothing left to export.", hint="Bring back some of the cuts.")
    chapters = sorted(ep.get("chapters", []), key=lambda c: c["time"])
    picks = ep.get("picks") or {}
    cold = [(float(p["start"]), float(p["end"])) for p in picks.get("cold_open", [])]
    outro = [(float(p["start"]), float(p["end"])) for p in picks.get("outro", [])]
    plan = render.Plan()

    if recipe is None:  # a plain episode: just the conversation
        pieces, to_finished, talk_end = render.conversation(voice_path, kept, 0.0)
        plan.pieces += pieces
        plan.talk_start, plan.talk_end = 0.0, talk_end
        plan.chapters = _chapters(chapters, to_finished, {})
        return plan

    kit_offset = talk_lufs - TALK_REFERENCE_LUFS  # keeps Nick's balance whatever level the talk is at
    theme = slot_choice(show, ep, "theme")
    montage = slot_choice(show, ep, "montage")
    stinger = slot_choice(show, ep, "stinger")
    intro, brk, end = recipe["intro"], recipe["break"], recipe["outro"]

    # ---- intro: cold open, theme, sound bites
    cursor = intro["cold_open_at"]
    for k, (a, b) in enumerate(cold):
        plan.pieces.append(render.Piece(src=voice_path, channels=1, start=a, end=b, at=cursor - (render.XFADE if k else 0),
                                        gain_db=intro["cold_open_gain_db"], fade_in=render.XFADE if k else 0.05,
                                        fade_out=render.XFADE, kind="voice", label="Cold open"))
        cursor += (b - a) - (render.XFADE if k else 0)
    cold_end = cursor if cold else 0.0
    if not cold and theme == "none" and montage == "none":
        shift = None  # nothing before the talk: start straight away
        talk_at = 0.0
    else:
        shift = (cold_end if cold else intro["cold_open_at"]) - intro["cold_open_end"]
        talk_at = intro["talk_at"] + shift
    if theme != "none" and shift is not None:
        t = intro["theme"]
        length = min(t["length"] + shift, length_of(theme))
        plan.pieces.append(render.Piece(src=kit_file(theme), channels=2, start=t["start"], end=t["start"] + length,
                                        at=t["at"], gain_db=t["gain_db"] + kit_offset, fade_in=0.05,
                                        fade_out=t["fade_out"], curve="exp", kind="music", label="Theme (intro)"))
    if montage != "none" and shift is not None:
        for asset, a, b, at, gain in intro["bites"]:
            plan.pieces.append(render.Piece(src=kit_file(asset), channels=2, start=a, end=b, at=at + shift,
                                            gain_db=gain + kit_offset, fade_in=0.03, fade_out=0.08,
                                            kind="bite", label="Sound bite"))

    # ---- the conversation, with a break before each segment after the first
    gap = 0.0
    if theme != "none":
        gap += brk["alone"]
    if stinger != "none":
        gap += brk["stinger_length"]
    segment_starts = [c["time"] for c in chapters[1:]] if gap > 0 else []
    breaks = {t: gap for t in segment_starts}
    pieces, to_finished, talk_end = render.conversation(voice_path, kept, talk_at, breaks)
    plan.pieces += pieces
    plan.talk_start, plan.talk_end = talk_at, talk_end
    stinger_at: dict[float, float] = {}
    for n, t in enumerate(segment_starts):
        resume = to_finished(t)  # where the next segment's talking starts
        stop = resume - gap  # where the previous segment's talking stopped
        if theme != "none":
            src_start = brk["theme_starts"][n % len(brk["theme_starts"])]
            length = brk["lead_in"] + brk["alone"]
            plan.pieces.append(render.Piece(src=kit_file(theme), channels=2, start=src_start, end=src_start + length,
                                            at=stop - brk["lead_in"], gain_db=brk["theme_gain_db"] + kit_offset,
                                            fade_in=brk["lead_in"] + 1.0, fade_out=brk["fade_out"], curve="exp",
                                            kind="music", label="Theme (break)"))
        if stinger != "none":
            s_len = min(brk["stinger_length"], length_of(stinger))
            at = resume - brk["stinger_length"]
            plan.pieces.append(render.Piece(src=kit_file(stinger), channels=2, start=0.0, end=s_len, at=at,
                                            gain_db=brk["stinger_gain_db"] + kit_offset, fade_in=0.07, fade_out=0.05,
                                            kind="effect", label="Stinger"))
            stinger_at[t] = at

    # ---- the ending: theme under the last words, sign-off clips, theme fades away
    cursor = talk_end + end["clips_after"]
    for k, (a, b) in enumerate(outro):
        plan.pieces.append(render.Piece(src=voice_path, channels=1, start=a, end=b, at=cursor - (render.XFADE if k else 0),
                                        gain_db=end["clips_gain_db"], fade_in=render.XFADE if k else 0.05,
                                        fade_out=render.XFADE, kind="voice", label="Sign-off"))
        cursor += (b - a) - (render.XFADE if k else 0)
    if theme != "none":
        stop = (cursor + end["tail"]) if outro else (talk_end + end["no_clips_tail"])
        start_at = talk_end - end["lead_in"]
        length = min(stop - start_at, length_of(theme))
        plan.pieces.append(render.Piece(src=kit_file(theme), channels=2, start=0.0, end=length, at=start_at,
                                        gain_db=end["theme_gain_db"] + kit_offset, fade_in=end["lead_in"] + 2.4,
                                        fade_out=end["fade_out"], curve="exp", kind="music", label="Theme (ending)"))
    plan.chapters = _chapters(chapters, to_finished, stinger_at)
    return plan


def _chapters(chapters: list[dict[str, Any]], to_finished: Callable[[float], float],
              stinger_at: dict[float, float]) -> list[dict[str, Any]]:
    """Chapter times in the finished file. The first starts at 0:00 (so the intro belongs to it);
    later ones start with their segment's stinger, a beat before the talking."""
    out = []
    for n, c in enumerate(chapters):
        t = 0.0 if n == 0 else stinger_at.get(c["time"], to_finished(c["time"]))
        out.append({"time": round(t, 3), "title": c["title"]})
    return out


def describe(plan: render.Plan) -> dict[str, Any]:
    """A short, readable outline of the render (for the export record and for Claude)."""
    kinds: dict[str, int] = {}
    for p in plan.pieces:
        kinds[p.label] = kinds.get(p.label, 0) + 1
    return {"length": round(plan.total, 3), "talk_starts": round(plan.talk_start, 3), "talk_ends": round(plan.talk_end, 3),
            "pieces": kinds, "chapters": plan.chapters}
