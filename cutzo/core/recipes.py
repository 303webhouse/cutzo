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

Changed after Nick heard the first rebuild (Oct 14, 2026): voices over the intro and ending music
were a bit too low. The theme now dips 5 dB whenever someone talks over it ("duck_db"), the cold
open and sign-off clips are 1.5 dB louder, and the ending theme is 3 dB softer. Measured on Season
Debrief Part 2, voices went from 6 LU (cold open) and 1 LU (sign-off) above the music to 12 and 10.

Extras (Oct 15, 2026): one-off pieces for one episode (Nick's word; `ep["inserts"]` in project.json,
because `ep["extras"]` already holds the export's captions and chapter-list files). Each is a sound or
video file of Nick's, or a show-kit item, played at a time in the recording: the talk pauses there,
the extras play one after another, and the talk goes on. Extras at a chapter play in its pause or
break, after the break music and before the page flip or stinger, like the haiku in Spooky Sode.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import render
from . import shows as showlib
from .errors import CutzoError

TALK_REFERENCE_LUFS = -19.2

# Extras (see above)
EXTRA_KINDS = ("voice", "music", "video")
EXTRA_BREATH = 0.5  # after a spoken extra, a beat before the page flip, stinger or talk
EXTRA_MUSIC_LU = -3.0  # a music extra without a known level sits this far below the talk
EXTRA_MUSIC_FADE = 7.6  # a music extra cut short fades out over this long (Nick's 93696 after the haiku)
EXTRA_VIDEO_FADE = 0.3  # a video extra dissolves in and out over this long

THE_DIG: dict[str, Any] = {
    "intro": {
        "cold_open_at": 0.5,
        "cold_open_gain_db": 5.5,  # Premiere had 4.0
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
        "theme_gain_db": -2.55,  # Premiere had 0.45
        "clips_after": 3.0,  # sign-off clips start this long after the talking ends
        "clips_gain_db": 8.0,  # Premiere had 6.5
        "tail": 7.2,  # theme keeps playing this long after the last clip
        "fade_out": 10.0,
        "no_clips_tail": 20.0,
    },
    "duck_db": 5.0,  # the theme dips this much while someone talks over it
    "max_cold_open": 45.0,
    "max_outro": 60.0,
}

# The New Heretics, copied from Nick's Spooky Sode edit in Premiere (Oct 31, 2025), with levels and
# fade shapes measured in the finished JW Ghost Stories video (Oct 15, 2026):
#
#   Teaser   Nick's picks from the talk, from 0:01.5, over a still of the montage's first frame
#            with the spectrum. It ends exactly when the montage's music starts.
#   Montage  the cult-leader montage with its own sound (the intro music and two voiceover lines),
#            starting 1.888 s before the teaser ends. The talk starts 83.7 s into the montage, as the
#            music fades; the episode title fades in at 91.2 s; the background loop takes over at 93.4 s.
#   Talk     the background loop (with the show's name), the episode title and the spectrum. Each new
#            topic (chapter) gets a page flip in a 2 s pause.
#   Breaks   before each chapter marked as a new segment: a music cue fades in under the last
#            seconds of talk (title and spectrum fade out, break visuals fade in), plays to its end,
#            a page flip, and the talk comes back with the title and spectrum.
#   Ending   the ending music fades in under the last minute of talk with the break visuals; as the
#            talk ends, the montage's opening frame (the red scene, no show name) takes over, then
#            Full Flames under the sign-off picks, the music staying low under them, and the picture
#            fades to black as the music ends.
NEW_HERETICS: dict[str, Any] = {
    # Levels as in the YouTube video of Spooky Sode, JW Ghost Stories.mp4 (re-measured Oct 15 in LUFS, each
    # stretch against the same stretch of its source; Nick's podcast MP3 is a separate export whose only
    # difference is 239_short2, 3.3 dB louder). The talk there: -16.0 LUFS; every level is relative to it.
    "talk_reference_lufs": -16.0,
    "teaser": {"at": 1.5, "gain_db": 1.8, "spectrum_early": 0.37, "spectrum_fade_in": 1.87},
    "montage": {"music_at": 1.888, "talk_at": 83.684, "title_at": 91.191, "background_at": 93.36,
                "gain_db": -5.3, "spectrum_early": 0.3, "held_frame": 87.354},
    # Under the talk Nick holds the montage's frame at 87.354 s (the red scene with the show's name: his
    # Premiere project's frame holds, read Oct 15, 2026; the last frame, 95 s in, differs a little) and screens
    # the fire loop over it at 2.03x speed (4275 s of the loop in 2104.5 s), so it flares about once a
    # minute; the break visuals and Full Flames are screened too (measured against JW Ghost Stories).
    "background_speed": 4275.086 / 2104.50,
    "no_montage_gap": 1.0,  # without the montage, the talk starts this long after the teaser
    "fade_back": 1.0,  # title and spectrum fade back in over this long when the talk comes back
    # Music cues: Nick's level (dB from the kit file to the finished episode) and how long each fades
    # in under the talk, from Spooky Sode (short1 and 93696 from his 2024 template). None: not measured,
    # so the level is set by loudness (see break_level_lu / ending_level_lu).
    "cues": {
        "tnh-cue-239-short3": (-9.4, 28.83),
        "tnh-cue-239-short2": (-11.8, 14.18),
        "tnh-cue-239-short1": (None, 4.6),
        "tnh-cue-93696": (-0.5, 25.7),  # from the podcast MP3 (it plays only in the haiku part of a break)
        "tnh-cue-239-full": (-17.2, 60.527),
        "tnh-page-flip": (-0.1, None),
    },
    "break_cues": ["tnh-cue-239-short3", "tnh-cue-239-short2", "tnh-cue-239-short1"],
    "break_level_lu": -3.2,  # a break cue on its own sits this far below the talk...
    "ending_level_lu": -14.3,  # ...and the ending music under the sign-off this far
    "default_lead_in": 14.18,
    "break": {"visuals_start": 0.701, "visuals_late": 0.5, "visuals_out": 1.0, "flip_before_end": 1.268,
              "resume_after_end": 0.3, "no_music_gap": 1.0,
              # before extras (Spooky Sode's haiku) the cue plays on its own for at most 22.1 s, then fades
              # out over 3.9 s (Nick cut 239_short3 there; the shorter cues are never that long anyway)
              "alone_before_extras": 22.122, "cut_fade": 3.87},
    "topic": {"flip_after": 0.2},  # a new topic: a pause holding the page flip, which starts this late
    "ending": {"cue_start": 0.367, "lead_in": 60.527, "visuals_late": 0.07, "visuals_fade_short": 2.4,
               "visuals_until": 15.6, "flames_at": 15.115, "flames_start": 5.339, "flames_fade_in": 0.5,
               "signoff_at": 18.452, "signoff_gain_db": 0.5, "fade_to_black": 31.966, "after_signoff": 3.0,
               "no_signoff": 40.0, "no_signoff_fade": 8.0, "title_early": 0.2,
               # under the ending visuals and Full Flames: the montage's opening frame (0.434 s in, before
               # any footage or the show's name), held, dissolving in just before the talk ends
               "backdrop_at": -0.834, "backdrop_frame": 0.434, "backdrop_fade_in": 1.0},
    "flip_level_lu": -12.9,  # another transition sound: this far below the talk (the page flip's level)
    "loop_fade": 1.0,  # a background loop that runs out starts again with this long a dissolve
}

RECIPES = {"the-dig": THE_DIG, "the-new-heretics": NEW_HERETICS}


def slot_choice(show: dict[str, Any], ep: dict[str, Any], slot: str) -> str:
    for s in show.get("slots", []):
        if s["slot"] == slot:
            return (ep.get("assets") or {}).get(slot, s["default"])
    return "none"


@dataclass
class Extra:
    """One extra, ready to place: what plays and which part of it. `src` is a kit item id (start and end
    are then times in the kit file), or "insert:<id>" for a file of Nick's, whose working copies (sound
    and picture) hold just the part used, so start is 0 there."""
    item: dict[str, Any]
    src: str
    kind: str  # voice, music or video
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start

    @property
    def has_sound(self) -> bool:
        return bool(self.item.get("has_sound", True))

    @property
    def starts_late(self) -> bool:
        """Starts after the beginning of its file (so it may start mid-sound)."""
        return float(self.item.get("start") or 0.0) > 0.05

    @property
    def cut_short(self) -> bool:
        """Stops before the end of its file."""
        end = float(self.item.get("end") or self.item.get("seconds") or 0.0)
        return end < float(self.item.get("seconds") or end) - 0.05


def extras_by_time(ep: dict[str, Any], chapters: list[dict[str, Any]], talk_until: float) -> dict[float, list[Extra]]:
    """The episode's extras grouped by the recording time where the talk pauses for them, in the order
    they were added. One placed at a chapter follows the chapter if it moves; one at or after
    `talk_until` (where the kept talk ends) is left out, since there's no talk after it to pause."""
    times = {c["id"]: float(c["time"]) for c in chapters if c.get("id")}
    out: dict[float, list[Extra]] = {}
    for item in ep.get("inserts") or []:
        t = times.get(item.get("chapter") or "")
        if t is None:
            t = float(item["time"])
        start = float(item.get("start") or 0.0)
        end = float(item.get("end") or item.get("seconds") or 0.0)
        if end - start < 0.05 or t >= talk_until - 1e-6:
            continue
        kind = item.get("kind") if item.get("kind") in EXTRA_KINDS else "voice"
        if item.get("asset"):
            x = Extra(item, item["asset"], kind, start, end)
        else:
            x = Extra(item, f"insert:{item['id']}", kind, 0.0, end - start)
        out.setdefault(t, []).append(x)
    return out


def _extra_gain(x: Extra, talk_lufs: float, loudness_of: Callable[[str], float | None] | None,
                music_lu: float = EXTRA_MUSIC_LU, known: float | None = None) -> float:
    """An extra's level: spoken ones (and video) as loud as the talk, music `music_lu` below it, unless the
    kit knows Nick's level for it (`known`, already relative to this episode's talk); plus the extra's
    own adjustment. The talk plays in both stereo channels, so its stereo loudness is 3 dB above the
    one-channel measurement; extras are measured in stereo."""
    own = float(x.item.get("gain_db") or 0.0)
    if known is not None:
        return known + own
    lufs = loudness_of(x.src) if loudness_of and x.has_sound else None
    if lufs is None:
        return own
    target = talk_lufs + render.MONO_IN_STEREO_DB + (music_lu if x.kind == "music" else 0.0)
    return target - lufs + own


def _extra_pieces(plan: render.Plan, extras: list[Extra], at: float, kit_file: Callable[[str], Path],
                  gain_of: Callable[[Extra], float]) -> list[tuple[Extra, float]]:
    """The extras' sound, one after another from `at`. Returns each extra with its start time."""
    placed, cursor = [], at
    for x in extras:
        if x.has_sound:
            if x.kind == "music":
                fade_in, fade_out = 0.02, (min(EXTRA_MUSIC_FADE, 0.15 * x.length) if x.cut_short else 0.05)
            else:
                fade_in, fade_out = (0.02 if x.starts_late else 0.005), (0.05 if x.cut_short else 0.01)
            plan.pieces.append(render.Piece(src=kit_file(x.src), channels=2, start=x.start, end=x.end, at=cursor,
                                            gain_db=gain_of(x), fade_in=fade_in, fade_out=fade_out,
                                            kind="music" if x.kind == "music" else "bite", label="Extra"))
        placed.append((x, cursor))
        cursor += x.length
    return placed


def _cut_out(windows: list[tuple[float, float, float, float]], spans: list[tuple[float, float]],
             fade: float = EXTRA_VIDEO_FADE) -> list[tuple[float, float, float, float]]:
    """(start, end, fade in, fade out) windows with the spans taken out (where a video extra covers the frame)."""
    for x, y in spans:
        out = []
        for a, b, fi, fo in windows:
            if y <= a or x >= b:
                out.append((a, b, fi, fo))
                continue
            if x > a:
                out.append((a, x, fi, fade))
            if y < b:
                out.append((y, b, fade, fo))
        windows = out
    return windows


def plan_episode(ep: dict[str, Any], show: dict[str, Any], *, voice_path: Path, talk_lufs: float,
                 kit_file: Callable[[str], Path], length_of: Callable[[str], float],
                 loudness_of: Callable[[str], float | None] | None = None) -> render.Plan:
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
    extras = extras_by_time(ep, chapters, kept[-1][1])
    plan = render.Plan()

    def gain_of(x: Extra) -> float:
        return _extra_gain(x, talk_lufs, loudness_of)

    if recipe is None:  # a plain episode: just the conversation (and any extras)
        gaps = {t: sum(x.length for x in xs) for t, xs in extras.items()}
        pieces, to_finished, talk_end = render.conversation(voice_path, kept, 0.0, gaps)
        plan.pieces += pieces
        for t, xs in extras.items():
            _extra_pieces(plan, xs, to_finished(t) - gaps[t], kit_file, gain_of)
        plan.talk_start, plan.talk_end = 0.0, talk_end
        plan.chapters = _chapters(chapters, to_finished, {})
        return plan

    if recipe is NEW_HERETICS:
        return _new_heretics(ep, show, recipe, voice_path=voice_path, talk_lufs=talk_lufs, kit_file=kit_file,
                             length_of=length_of, loudness_of=loudness_of, kept=kept, chapters=chapters,
                             cold=cold, outro=outro, extras=extras)
    plan.duck_db = float(recipe.get("duck_db", 0.0))
    plan.talk_reference_lufs = TALK_REFERENCE_LUFS
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

    def extras_length(xs: list[Extra], in_break: bool) -> float:
        # in a break, a spoken extra gets a beat before the stinger
        return sum(x.length for x in xs) + (EXTRA_BREATH if in_break and xs and xs[-1].kind != "music" else 0.0)

    for t, xs in extras.items():
        breaks[t] = breaks.get(t, 0.0) + extras_length(xs, t in breaks)
    pieces, to_finished, talk_end = render.conversation(voice_path, kept, talk_at, breaks)
    plan.pieces += pieces
    plan.talk_start, plan.talk_end = talk_at, talk_end
    for t, xs in extras.items():  # extras in a plain pause; those in a break are placed below
        if t not in segment_starts:
            _extra_pieces(plan, xs, to_finished(t) - extras_length(xs, False), kit_file, gain_of)
    stinger_at: dict[float, float] = {}
    for n, t in enumerate(segment_starts):
        xs = extras.get(t, [])
        resume = to_finished(t)  # where the next segment's talking starts
        stop = resume - gap - extras_length(xs, True)  # where the previous segment's talking stopped
        if xs:  # after the theme plays on its own, before the stinger
            _extra_pieces(plan, xs, stop + (brk["alone"] if theme != "none" else 0.0), kit_file, gain_of)
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


def _voice_pieces(voice_path: Path, picks: list[tuple[float, float]], at: float, gain_db: float,
                  label: str) -> tuple[list[render.Piece], float]:
    """Picks from the recording one after another from `at`, joined with short crossfades."""
    pieces, cursor = [], at
    for k, (a, b) in enumerate(picks):
        x = render.XFADE if k else 0.0
        pieces.append(render.Piece(src=voice_path, channels=1, start=a, end=b, at=cursor - x, gain_db=gain_db,
                                   fade_in=x if k else 0.05, fade_out=render.XFADE, kind="voice", label=label))
        cursor += (b - a) - x
    return pieces, cursor


def _loop(layers: list[render.Layer], asset: str, at: float, until: float, length: float, fade: float,
          label: str, *, speed: float = 1.0, blend: str = "normal") -> None:
    """A video from its start for as long as needed, starting again (with a dissolve) when it runs out.
    `length` is the video's own length; at `speed` it lasts length / speed on the timeline."""
    length = length / speed
    if length <= 2 * fade:  # too short to loop (or unknown): one long run
        length = max(until - at, 0.0)
    start_at, first = at, True
    while until - start_at > 0.05:
        run = min(until - start_at, length)
        layers.append(render.Layer("video", asset, at=start_at, length=run, fade_in=0.0 if first else fade,
                                   fade_out=0.0 if start_at + run >= until - 1e-6 or blend == "normal" else fade,
                                   label=label, blend=blend, speed=speed))
        if start_at + run >= until - 1e-6:
            break
        start_at, first = start_at + run - fade, False


def _new_heretics(ep: dict[str, Any], show: dict[str, Any], recipe: dict[str, Any], *, voice_path: Path,
                  talk_lufs: float, kit_file: Callable[[str], Path], length_of: Callable[[str], float],
                  loudness_of: Callable[[str], float | None] | None, kept: list[tuple[float, float]],
                  chapters: list[dict[str, Any]], cold: list[tuple[float, float]],
                  outro: list[tuple[float, float]], extras: dict[float, list[Extra]]) -> render.Plan:
    """The New Heretics' layout (see NEW_HERETICS), sound and picture."""
    ref = recipe["talk_reference_lufs"]
    plan = render.Plan(talk_reference_lufs=ref)
    pic = render.Picture()
    plan.picture = pic
    # Keeps Nick's balance whatever level the talk is at. The talk is measured on the one-channel voice
    # track, but in the stereo mix it plays in both channels, 3 dB louder; Nick's levels were measured on
    # his stereo video, so compare like with like (without this, every cue came out 3 dB low).
    kit_offset = talk_lufs + render.MONO_IN_STEREO_DB - ref
    T, M, B, E = recipe["teaser"], recipe["montage"], recipe["break"], recipe["ending"]
    montage = slot_choice(show, ep, "montage")
    background = slot_choice(show, ep, "background")
    break_music = slot_choice(show, ep, "break_music")
    break_visuals = slot_choice(show, ep, "break_visuals")
    ending_music = slot_choice(show, ep, "ending_music")
    ending_visuals = slot_choice(show, ep, "ending_visuals")
    flip = slot_choice(show, ep, "transition")
    flip_len = length_of(flip) if flip != "none" else 0.0

    def level(asset: str, relative_lu: float, fallback: float) -> float:
        known = recipe["cues"].get(asset, (None, None))[0]
        if known is not None:
            return known
        lufs = loudness_of(asset) if loudness_of else None
        return fallback if lufs is None else ref + relative_lu - lufs

    def transition(c: dict[str, Any]) -> tuple[str, float, float, str] | None:
        """The sound at a chapter: its voiced title if it has one (The New Heretics' old segment names),
        otherwise the transition sound (the page flip). Returns (asset, length, gain, label)."""
        voiced = showlib.segment_title_for(show, c["title"])
        if voiced:
            return voiced["id"], length_of(voiced["id"]), level(voiced["id"], 0.0, 0.0), "Voiced title"
        if flip != "none":
            return flip, flip_len, level(flip, recipe["flip_level_lu"], -0.3), "Page flip"
        return None

    def play(sound: tuple[str, float, float, str], at: float) -> None:
        asset, length, gain, label = sound
        plan.pieces.append(render.Piece(src=kit_file(asset), channels=2, start=0.0, end=length, at=at,
                                        gain_db=gain + kit_offset, fade_in=0.01, fade_out=0.05, kind="effect",
                                        label=label))

    def extra_gain(x: Extra) -> float:
        known = recipe["cues"].get(x.src, (None, None))[0]  # a kit cue with Nick's own level (93696)
        return _extra_gain(x, talk_lufs, loudness_of, recipe["break_level_lu"],
                           None if known is None else known + kit_offset)

    video_spans: list[tuple[float, float]] = []  # where a video extra covers the frame (no spectrum there)

    def extra_videos(placed: list[tuple[Extra, float]]) -> None:
        for x, at in placed:
            if x.kind == "video":
                pic.layers.append(render.Layer("video", x.src, at=at, length=x.length, start=x.start,
                                               fade_in=EXTRA_VIDEO_FADE, fade_out=EXTRA_VIDEO_FADE, label="Extra video"))
                video_spans.append((at, at + x.length))

    # ---- teaser and montage
    teaser_len = sum(b - a for a, b in cold) - render.XFADE * max(0, len(cold) - 1)
    teaser_at = T["at"] if cold else 0.0
    m: float | None = None
    if montage != "none":
        music = teaser_at + teaser_len if cold else M["music_at"]
        m = music - M["music_at"]
        if m < 0:  # a very short teaser: start it a little later instead
            teaser_at, m = teaser_at - m, 0.0
        talk_at = m + M["talk_at"]
    else:
        talk_at = teaser_at + teaser_len + recipe["no_montage_gap"] if cold else 0.5
    pieces, teaser_end = _voice_pieces(voice_path, cold, teaser_at, T["gain_db"], "Teaser")
    plan.pieces += pieces
    titles: list[tuple[float, float, float, float]] = []  # when the episode title shows
    spectrum: list[tuple[float, float, float, float]] = []
    if cold:
        spectrum.append((max(0.0, teaser_at - T["spectrum_early"]), teaser_end, T["spectrum_fade_in"], 0.0))

    # ---- the conversation: a pause before each new segment (music break) and each new topic
    segments = [c for c in chapters[1:] if c.get("segment")]
    topics = [c for c in chapters[1:] if not c.get("segment")]
    cycle = recipe["break_cues"]
    gaps: dict[float, float] = {}
    breaks = []
    for n, c in enumerate(segments):
        cue = (cycle[n % len(cycle)] if break_music == "tnh-cues-theocretus"
               else None if break_music == "none" else break_music)
        sound = transition(c)
        sound_len = sound[1] if sound else 0.0
        xs = extras.get(float(c["time"]), [])
        if cue:
            length = length_of(cue)
            lead = min(recipe["cues"].get(cue, (None, None))[1] or recipe["default_lead_in"], max(1.0, length - 2.0))
            if xs:  # extras follow: a long cue is cut short
                length = min(length, lead + B["alone_before_extras"])
        else:
            length = lead = 0.0
        # After the talk stops: the break music on its own, then any extras, then the page flip (which
        # starts before the end of the last music, as in Nick's breaks) and the talk.
        if cue or xs:
            ends_in_music = xs[-1].kind == "music" if xs else True
            if sound:
                tail = (sound_len - B["flip_before_end"]) if ends_in_music else EXTRA_BREATH + sound_len
            else:
                tail = B["resume_after_end"] if ends_in_music else EXTRA_BREATH
            gap = (length - lead) + sum(x.length for x in xs) + max(0.0, tail)
        else:
            gap = sound_len + B["no_music_gap"]
        breaks.append((c, cue, length, lead, gap, sound, xs))
        gaps[float(c["time"])] = gaps.get(float(c["time"]), 0.0) + gap
    for c in topics:
        sound = transition(c)
        xs = extras.get(float(c["time"]), [])
        if sound or xs:
            gaps[float(c["time"])] = gaps.get(float(c["time"]), 0.0) + sum(x.length for x in xs) + (
                recipe["topic"]["flip_after"] + sound[1] if sound else 0.0)
    chapter_times = {float(c["time"]) for c in chapters[1:]}
    for t, xs in extras.items():  # extras away from chapters: the talk just pauses for them
        if t not in chapter_times:
            gaps[t] = gaps.get(t, 0.0) + sum(x.length for x in xs)
    pieces, to_finished, talk_end = render.conversation(voice_path, kept, talk_at, gaps)
    plan.pieces += pieces
    plan.talk_start, plan.talk_end = talk_at, talk_end
    chapter_at: dict[str, float] = {}
    title_from = (m + M["title_at"]) if m is not None else max(0.0, talk_at - 0.5)
    title_in = recipe["fade_back"]
    spectrum_from = max(0.0, talk_at - M["spectrum_early"])
    for c, cue, length, lead, gap, sound, xs in breaks:
        resume = to_finished(float(c["time"]))
        stop = resume - gap
        fade = max(0.5, lead - B["visuals_late"]) if cue else 0.5
        titles.append((title_from, stop, title_in, fade))
        spectrum.append((spectrum_from, stop, recipe["fade_back"], fade))
        if cue:
            cut = length < length_of(cue) - 0.05
            plan.pieces.append(render.Piece(src=kit_file(cue), channels=2, start=0.0, end=length, at=stop - lead,
                                            gain_db=level(cue, recipe["break_level_lu"], -9.0) + kit_offset,
                                            fade_in=lead, fade_out=B["cut_fade"] if cut else 0.0, curve="premiere",
                                            kind="music", label="Break music"))
        placed = _extra_pieces(plan, xs, stop + length - lead, kit_file, extra_gain)
        # The break visuals show under the music: from the cue's start, paused (with a 1 s fade) while a
        # spoken or video extra plays, back for music extras (carrying on where they stopped), until just
        # after the talk comes back. A spoken extra gets the spectrum, as Nick's haiku did.
        if break_visuals != "none":
            runs: list[tuple[float, float, float, float]] = []
            vis_at = stop - lead + B["visuals_late"]
            run = (vis_at, stop - vis_at) if cue else None
            for x, at in placed:
                if x.kind == "music" and run is None:
                    run = (at, 1.0)
                elif x.kind != "music" and run is not None:
                    runs.append((run[0], at, run[1], 1.0))
                    run = None
            if run is not None:
                runs.append((run[0], resume + B["visuals_out"], run[1], B["visuals_out"]))
            source = B["visuals_start"]
            for a, b, fi, fo in runs:
                if b - a > 0.1:
                    pic.layers.append(render.Layer("video", break_visuals, at=a, length=b - a, start=source,
                                                   fade_in=min(fi, b - a), fade_out=fo, label="Break visuals",
                                                   blend="screen"))
                source += b - a
        voiced: list[list[float]] = []
        for x, at in placed:
            if x.kind == "voice":
                if voiced and abs(voiced[-1][1] - at) < 1e-6:
                    voiced[-1][1] = at + x.length
                else:
                    voiced.append([at, at + x.length])
        spectrum += [(a, b, 0.0, 0.0) for a, b in voiced]
        extra_videos(placed)
        if sound:
            play(sound, resume - sound[1])
        chapter_at[c["id"]] = resume - (sound[1] if sound else 0.0)
        title_from, spectrum_from = resume, resume
    for c in topics:
        resume = to_finished(float(c["time"]))
        sound = transition(c)
        xs = extras.get(float(c["time"]), [])
        before = recipe["topic"]["flip_after"] + sound[1] if sound else 0.0
        extra_videos(_extra_pieces(plan, xs, resume - before - sum(x.length for x in xs), kit_file, extra_gain))
        if sound:
            play(sound, resume - sound[1])
        chapter_at[c["id"]] = resume - (sound[1] if sound else 0.0)
    for t, xs in extras.items():
        if t not in chapter_times:
            extra_videos(_extra_pieces(plan, xs, to_finished(t) - sum(x.length for x in xs), kit_file, extra_gain))

    # ---- the ending: music under the last minute of talk, then the sign-off over Full Flames
    signoff_at = talk_end + E["signoff_at"]
    pieces, signoff_end = _voice_pieces(voice_path, outro, signoff_at, E["signoff_gain_db"], "Sign-off")
    plan.pieces += pieces
    lead = 0.0
    cue_end = talk_end
    if ending_music != "none":
        start = E["cue_start"] if ending_music == "tnh-cue-239-full" else 0.0
        length = length_of(ending_music) - start
        lead = min(E["lead_in"], max(5.0, length - 30.0))
        cue_end = talk_end - lead + length
    end = max(cue_end, signoff_end + E["after_signoff"], talk_end + E["flames_at"] + 5.0) if outro \
        else talk_end + E["no_signoff"]
    if ending_music != "none":
        cut = end < cue_end - 0.01
        played = min(cue_end, end) - (talk_end - lead)
        plan.pieces.append(render.Piece(src=kit_file(ending_music), channels=2, start=start, end=start + played,
                                        at=talk_end - lead,
                                        gain_db=level(ending_music, recipe["ending_level_lu"], -17.5) + kit_offset,
                                        fade_in=lead, fade_out=E["no_signoff_fade"] if cut else 0.0, curve="premiere",
                                        kind="music", label="Ending music"))
    end_lead = max(lead, 5.0)
    titles.append((title_from, talk_end, title_in, max(0.5, end_lead - E["title_early"])))
    spectrum.append((spectrum_from, talk_end, recipe["fade_back"], end_lead))
    if montage != "none":
        backdrop = talk_end + E["backdrop_at"]
        pic.layers.append(render.Layer("still", montage, at=backdrop, length=end - backdrop, start=E["backdrop_frame"],
                                       fade_in=E["backdrop_fade_in"], label="Ending backdrop"))
    if break_visuals != "none":
        vis_at = talk_end - end_lead + E["visuals_late"]
        pic.layers.append(render.Layer("video", break_visuals, at=vis_at, length=talk_end + E["visuals_until"] - vis_at,
                                       fade_in=max(0.5, end_lead - E["visuals_fade_short"]), fade_out=1.0,
                                       label="Ending visuals", blend="screen"))
    flames_at = talk_end + E["flames_at"]
    if ending_visuals != "none":
        pic.layers.append(render.Layer("video", ending_visuals, at=flames_at, length=end - flames_at,
                                       start=E["flames_start"], fade_in=E["flames_fade_in"], label="Ending visuals",
                                       blend="screen"))
    pic.fade_to_black = min(E["fade_to_black"], max(0.0, end - talk_end))

    # ---- picture, bottom to top: opening still, montage, background, title, break and ending visuals
    under: list[render.Layer] = []
    if m is not None:
        if m > 0:
            under.append(render.Layer("still", montage, at=0.0, length=m, label="Opening still"))
        mlen = length_of(montage)
        under.append(render.Layer("video", montage, at=m, length=mlen, label="Opening montage"))
        under.append(render.Layer("still", montage, at=m + mlen, length=max(0.0, end - (m + mlen)),
                                  start=min(M["held_frame"], max(0.0, mlen - 0.1)), label="Held title card"))
        plan.pieces.append(render.Piece(src=kit_file(montage), channels=2, start=0.0, end=mlen, at=m,
                                        gain_db=M["gain_db"] + kit_offset, kind="music", label="Opening montage"))
        bg_from = m + M["background_at"]
    else:
        bg_from = teaser_end if cold else 0.0
        if bg_from > 0:
            under.append(render.Layer("still", background, at=0.0, length=bg_from, label="Opening still"))
    bg_until = (flames_at + 1.0) if ending_visuals != "none" else end
    if m is not None:  # over the held frame, as in Nick's edit
        _loop(under, background, bg_from, bg_until, length_of(background), recipe["loop_fade"], "Background",
              speed=recipe["background_speed"], blend="screen")
    else:  # no montage, so nothing held to screen it over: the loop as it is
        _loop(under, background, bg_from, bg_until, length_of(background), recipe["loop_fade"], "Background")
    for a, b, fi, fo in titles:
        if b - a > 0.1:
            under.append(render.Layer("title", "episode-title", at=a, length=b - a, fade_in=fi, fade_out=min(fo, b - a),
                                      label="Episode title"))
    pic.layers = under + pic.layers
    if slot_choice(show, ep, "spectrum") != "none":
        pic.spectrum = [(a, b, fi, min(fo, b - a)) for a, b, fi, fo in _cut_out(spectrum, video_spans) if b - a > 0.1]
    ordered = chapters
    plan.chapters = [{"time": 0.0 if n == 0 else round(max(0.0, chapter_at.get(c["id"], to_finished(c["time"]))), 3),
                      "title": c["title"]} for n, c in enumerate(ordered)]
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
    out = {"length": round(plan.total, 3), "talk_starts": round(plan.talk_start, 3), "talk_ends": round(plan.talk_end, 3),
           "pieces": kinds, "chapters": plan.chapters, "music_dips_db": plan.duck_db}
    if plan.picture:
        layers: dict[str, int] = {}
        for layer in plan.picture.layers:
            layers[layer.label] = layers.get(layer.label, 0) + 1
        out["picture"] = {"layers": layers, "spectrum_windows": len(plan.picture.spectrum),
                          "fade_to_black": round(plan.picture.fade_to_black, 3)}
    return out
