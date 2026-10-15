"""Nick's show kit: the backgrounds, intros, sounds and art each show uses.

The files live in Google Drive (mounted as G: on Nick's PC). In Phase 1 Cutzo
only lists them. Phase 2 (The Dig) and Phase 3 (The New Heretics) copy what each
show uses into F:\\Cutzo\\kit so renders never depend on Drive syncing.
Inventory taken from Drive on Oct 13, 2026.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

TNH = "The New Heretics"
DIG = "Denver Stiffs/The Dig"


def _a(asset_id: str, show: str, name: str, folder: str, kind: str, use: str,
       drive_id: str | None = None, note: str | None = None, size: str | None = None,
       seconds: float | None = None) -> dict[str, Any]:
    return {
        "id": asset_id,
        "show": show,
        "name": name,
        "folder": folder,
        "kind": kind,          # video | audio | image | project | style
        "use": use,            # plain-English role in the show
        "drive_id": drive_id,
        "note": note,
        "size": size,
        "seconds": seconds,  # length, for laying out an episode before the file is copied
        "location": "builtin" if kind == "style" else "drive",
    }


ASSETS: list[dict[str, Any]] = [
    # --- The New Heretics: backgrounds ----------------------------------
    _a("tnh-bg-loop-1hr", "the-new-heretics", "BG Loop (1 hr).mp4", f"{TNH}/YouTube BG/YouTube Files (FINAL)",
       "video", "Background loop", "1G9b6bkiH4SSR1HbP1YwsT6-eCxrgJb8G",
       "71 minutes; the show's name is part of the picture. Starts again with a 1 s fade if an episode runs longer.",
       seconds=4275.07),
    _a("tnh-fire-loop-1min", "the-new-heretics", "YT FIRE LOOP (1 MIN).mp4", f"{TNH}/YouTube BG/BG Animation",
       "video", "Background loop", "13ut6tFk2AlH5FBZ701YhpFONq7kngRc-", "Loops seamlessly for any length."),
    _a("tnh-fire-loop", "the-new-heretics", "YT Fire Loop.mp4", f"{TNH}/YouTube BG/BG Animation",
       "video", "Background loop", "13h_D_bZPsL4grmI8v7Bpt00eccvH9nN4"),
    _a("tnh-bg-master", "the-new-heretics", "YT BG Master (Final).mp4", f"{TNH}/YouTube BG/BG Animation",
       "video", "Background loop", "13aA3yBqfJS73Tj5rujK999RmF3uP8hxq"),
    _a("tnh-full-flames", "the-new-heretics", "Full Flames (Final).mp4", f"{TNH}/YouTube BG/YouTube Files (FINAL)",
       "video", "Ending visuals", "1GzPsEAtTapwjb4-7T2YUE-Aw02-EuFSo",
       "The outlines catch fire; plays under the sign-off, then fades to black.", seconds=119.99),
    _a("tnh-streaking-fire", "the-new-heretics", "Streaking Fire (4K 30p).mp4", f"{TNH}/YouTube BG/YouTube Files (FINAL)",
       "video", "Background loop", "1CAzA4dOLapj8_5R2hwvcZey3UgEVcVyk"),
    _a("tnh-streaking-logos-nobg", "the-new-heretics", "Streaking with Logos NO BG (4K30p).mp4",
       f"{TNH}/YouTube BG/YouTube Files (FINAL)", "video", "Logo overlay", "1EBnRui_sFksV8O-v7UNOv0aUUuIvJzLZ",
       "On black. Not used in recent episodes."),
    _a("tnh-music-loop", "the-new-heretics", "Music Loop (5 mins).mp4", f"{TNH}/YouTube BG/YouTube Files (FINAL)",
       "video", "Break visuals", "1FZK8g5ObRG_j2gXhkTa2rXLPWJ29UNkR",
       "Glowing icons over the background, shown during music breaks and the ending (no sound; really 64 minutes).",
       seconds=3830.83),
    # --- The New Heretics: intro, music, sounds, art -------------------
    _a("tnh-montage", "the-new-heretics", "YT Intro 10.18.24.mp4", f"{TNH}/YouTube BG/YouTube Files (FINAL)",
       "video", "Opening montage", note="The cult-leader montage. Its own sound is the intro music (TNH Cult Intro.mp3, "
       "from 1.9 s in) with two voiceover lines, so the clips always match the music.", seconds=95.095),
    _a("tnh-intro", "the-new-heretics", "YT Intro (Final).mp4", f"{TNH}/YouTube BG/Intro",
       "video", "Opening montage", "17DXsBBqHR5kktI7Kg_hXAl-7-W92MY-f",
       "An earlier version of the montage, with the same sound.", seconds=95.095),
    _a("tnh-title-sequence", "the-new-heretics", "YT Title Sequence.mp4", f"{TNH}/YouTube BG/Intro",
       "video", "Opening montage", "16YxGz4Hm7yh_4EwAGDq5Ms2pbuURm1Wj"),
    _a("tnh-cult-intro", "the-new-heretics", "TNH Cult Intro.mp3", f"{TNH}/Podcast/Intro", "audio", "Intro music",
       note="The montage's music on its own (the montage video already has it).", seconds=89.64),
    _a("tnh-cues-theocretus", "the-new-heretics", "Theocretus cues, in turn", "Built into Cutzo", "style", "Break music",
       note="239_short3 for the first break, 239_short2 for the second, 239_short1 for the third, then round again "
            "(Spooky Sode used short3 and short2)."),
    _a("tnh-cue-239-short1", "the-new-heretics", "239_short1_mnmal-barok_0018.wav", f"{TNH}/Podcast/Music/Theocretus",
       "audio", "Music cue", seconds=18.095),
    _a("tnh-cue-239-short2", "the-new-heretics", "239_short2_mnmal-barok_0033.wav", f"{TNH}/Podcast/Music/Theocretus",
       "audio", "Music cue", seconds=33.333),
    _a("tnh-cue-239-short3", "the-new-heretics", "239_short3_mnmal-barok_0063.wav", f"{TNH}/Podcast/Music/Theocretus",
       "audio", "Music cue", seconds=63.81),
    _a("tnh-cue-239-full", "the-new-heretics", "239_full_mnmal-barok_0159.wav", f"{TNH}/Podcast/Music/Theocretus",
       "audio", "Music cue", note="The ending music in Spooky Sode.", seconds=159.643),
    _a("tnh-cue-239-instrumental", "the-new-heretics", "239_instrumental1_mnmal-barok_0160.wav",
       f"{TNH}/Podcast/Music/Theocretus", "audio", "Music cue", seconds=160.0),
    _a("tnh-cue-93696", "the-new-heretics", "93696.wav", f"{TNH}/Podcast/Music", "audio", "Music cue",
       note="Played after the haiku in Spooky Sode.", seconds=109.83),
    _a("tnh-cue-god-of-love", "the-new-heretics", "God of Love.wav", f"{TNH}/Podcast/Music", "audio", "Music cue",
       seconds=134.92),
    _a("tnh-vo-listening", "the-new-heretics", "Listening to the New Heretics (huh).mp3", f"{TNH}/Podcast/Subtitles/Charlotte",
       "audio", "Voiceover line", note="Already in the montage's sound.", seconds=2.22),
    _a("tnh-vo-shame", "the-new-heretics", "Shame on you.mp3", f"{TNH}/Podcast/Subtitles/Charlotte", "audio",
       "Voiceover line", seconds=1.28),
    _a("tnh-vo-shame-2", "the-new-heretics", "Shame on you 2.mp3", f"{TNH}/Podcast/Subtitles/Charlotte", "audio",
       "Voiceover line", note="Already in the montage's sound.", seconds=1.097),
    _a("tnh-vo-cult-questionnaire", "the-new-heretics", "Cult Questionnaire.mp3", f"{TNH}/Podcast/Subtitles/Charlotte",
       "audio", "Voiceover line", seconds=1.332),
    _a("tnh-vo-haiku-intro", "the-new-heretics", "Haiku Intro.mp3", f"{TNH}/Podcast/Subtitles/Charlotte", "audio",
       "Voiceover line", note="Introduces the haiku in the first break.", seconds=6.034),
    _a("tnh-vo-logo-quote-intro", "the-new-heretics", "Logo Quote Intro.mp3", f"{TNH}/Podcast/Subtitles/Charlotte",
       "audio", "Voiceover line", seconds=2.821),
    _a("tnh-vo-define-your-terms", "the-new-heretics", "Define Your Terms.mp3", f"{TNH}/Podcast/Subtitles/Charlotte",
       "audio", "Voiceover line", seconds=1.567),
    _a("tnh-seg-the-new-heretics", "the-new-heretics", "The New Heretics.mp3", f"{TNH}/Podcast/Subtitles",
       "audio", "Voiced segment title", "1XLRb_BEKUK4Eh7YfFi-P4DD5EtIgy3wu", seconds=1.567),
    _a("tnh-seg-define-your-terms", "the-new-heretics", "Define Your Terms.mp3", f"{TNH}/Podcast/Subtitles",
       "audio", "Voiced segment title", "1XNOllIoA_fA-WDrEY_2jRqZKPNkcIW-O", seconds=1.437),
    _a("tnh-seg-why-should-we-care", "the-new-heretics", "Why Should We Care.mp3", f"{TNH}/Podcast/Subtitles",
       "audio", "Voiced segment title", "1XNs9qZXGOXIWO9sIHxHFZHbtNarEsRlW", seconds=1.332),
    _a("tnh-seg-building-a-better-apostasy", "the-new-heretics", "Building a Better Apostasy.mp3",
       f"{TNH}/Podcast/Subtitles", "audio", "Voiced segment title", "1XMtyRlF6bv-Jdjgyk8DQ1U0QX5o8cvU5", seconds=1.985),
    _a("tnh-seg-heretical-haiku", "the-new-heretics", "Heretical Haiku.mp3", f"{TNH}/Podcast/Subtitles",
       "audio", "Voiced segment title", "1XKNjyf0sjRc3qVMwApyC8KJW4CxH593u", seconds=7.602),
    _a("tnh-seg-logo-loses-us", "the-new-heretics", "Logo Loses Us.mp3", f"{TNH}/Podcast/Subtitles",
       "audio", "Voiced segment title", "1XL7pF-tQZTRh49Nl2s0OMD3ldgLB_2nO", seconds=3.161),
    _a("tnh-turn-the-page", "the-new-heretics", "Turn the Page.wav", f"{TNH}/Podcast/Subtitles",
       "audio", "Transition sound", "1Xt_oDh-55v7xczdKg-Snia0biWO2ckuX", seconds=3.007),
    _a("tnh-page-flip", "the-new-heretics", "Page Flip.wav", f"{TNH}/Podcast/Sounds", "audio", "Transition sound",
       seconds=1.904),
    _a("tnh-podcast-art", "the-new-heretics", "Podcast-BG-Image.jpg", f"{TNH}/Podcast/Graphics", "image", "Podcast art",
       note="The .ai originals stay in Illustrator; Cutzo uses the JPG."),
    # --- The New Heretics: references (not placed in episodes) ---------
    _a("tnh-ghost-stories", "the-new-heretics", "JW Ghost Stories.mp4", f"{TNH}/Podcast/Episodes/Spooky Sode/FINAL",
       "video", "Finished example to match", "1Ye_HNllPp9kU0MCu4pm73_l7yipOlOSZ",
       "Cutzo's version of Spooky Sode is checked against this.", "12.9 GB"),
    _a("tnh-spectrum-template", "the-new-heretics", "Audio Spectrum Template.aep", f"{TNH}/Podcast/Episodes",
       "project", "Reference only", note="After Effects file. Cutzo draws its own spectrum to match it."),
    _a("tnh-voice-template", "the-new-heretics", "Audio Adjustments.prproj", f"{TNH}/Podcast/Voice Template",
       "project", "Reference only", "1jYsIvroD-lVp6JfIFBzaJluWuPgRWNwP",
       "Your Premiere voice settings; Cutzo's Podcast voice starts from these."),
    # --- The Dig ---------------------------------------------------------
    _a("dig-theme", "the-dig", "Out of Flux - DABOOMJIGGLE.mp3", DIG, "audio", "Theme music",
       note="Opens and closes each episode and plays between segments."),
    _a("dig-montage", "the-dig", "Sound-bite montage", "Built into Cutzo", "style", "Intro sound bites",
       note="The bites from Not meant for this game, Doug Moe Greatness, Take that L and The Dig Intro Audio, "
            "timed as in Season Debrief Part 2."),
    _a("dig-intro-gabe", "the-dig", "Gabe Audio.mp3", f"{DIG}/Intro", "audio", "Intro clip"),
    _a("dig-intro-doug-moe", "the-dig", "Doug Moe Greatness.wav", f"{DIG}/Intro", "audio", "Intro clip"),
    _a("dig-intro-take-that-l", "the-dig", "Take that l on the way out.wav", f"{DIG}/Intro", "audio", "Intro clip"),
    _a("dig-intro-not-meant", "the-dig", "Not meant for this game.wav", f"{DIG}/Intro", "audio", "Intro clip"),
    _a("dig-intro-audio", "the-dig", "The Dig Intro Audio.wav", f"{DIG}/Old Files", "audio", "Intro clip"),
    _a("dig-sfx-three-digs", "the-dig", "Three Digs.wav", f"{DIG}/Sound Effects", "audio", "Sound effect"),
    _a("dig-sfx-twolves-win", "the-dig", "Twolves Win.wav", f"{DIG}/Sound Effects", "audio", "Sound effect"),
    _a("dig-sfx-national-anthem", "the-dig", "National Anthem .wav", f"{DIG}/Sound Effects", "audio", "Sound effect"),
    _a("dig-sfx-tom-haiku-intro", "the-dig", "Tom Haiku Intro.wav", f"{DIG}/Sound Effects", "audio", "Sound effect"),
    _a("dig-sfx-mining-chops", "the-dig", "Mining Chops.wav", f"{DIG}/Sound Effects", "audio", "Sound effect"),
    _a("dig-sfx-untitled", "the-dig", "untitled.wav", f"{DIG}/Sound Effects", "audio", "Sound effect"),
    _a("dig-jok-banner", "the-dig", "Jok Banner.jpg", DIG, "image", "Cover art"),
    _a("dig-final-example", "the-dig", "2025-26 Season Debrief Part 2 -- Impossible Trades.mp3",
       f"{DIG}/Season Debrief Part 2", "audio", "Finished example to match", "1nVwqJ3ffGcCpchUUk7Dz-unHaND3qhH3",
       "Cutzo's version of this episode is compared against it by ear."),
    # --- Built into Cutzo --------------------------------------------------
    _a("spectrum-ghost-bars", "*", "Ghost Stories spectrum", "Built into Cutzo", "style", "Audio spectrum",
       note="Cutzo's copy of your After Effects spectrum, matched to JW Ghost Stories.mp4."),
]

_BY_ID = {a["id"]: a for a in ASSETS}

NONE_ASSET = {"id": "none", "name": "None", "kind": "none", "use": "Leave this part out", "show": "*",
              "folder": "", "location": "builtin", "drive_id": None, "note": None, "size": None}


def get_asset(asset_id: str) -> dict[str, Any] | None:
    if asset_id == "none":
        return dict(NONE_ASSET)
    asset = _BY_ID.get(asset_id)
    return dict(asset) if asset else None


def list_assets(show: str | None = None) -> list[dict[str, Any]]:
    out = []
    for asset in ASSETS:
        if show and asset["show"] not in (show, "*"):
            continue
        out.append(dict(asset))
    return out


def drive_url(asset: dict[str, Any]) -> str | None:
    if not asset.get("drive_id"):
        return None
    return f"https://drive.google.com/file/d/{asset['drive_id']}/view"


def source_path(asset: dict[str, Any], drive: Path | None) -> Path | None:
    """Where the asset's file is on this computer (Google Drive for desktop), if it has a file."""
    if not drive or asset.get("location") != "drive" or asset.get("kind") not in ("audio", "video", "image"):
        return None
    return Path(drive).joinpath(*asset["folder"].split("/"), asset["name"])


def local_copy(asset_id: str, *, kit_dir: Path, drive: Path | None, check=None) -> Path:
    """The asset's sound as a stereo working file in Cutzo's kit folder.

    Made the first time a render needs it, and again whenever the file in Drive changes, so
    renders never wait on Drive syncing."""
    from ..tools import ffmpeg  # here, so listing the kit never needs FFmpeg
    from .errors import CutzoError, NotFound
    from .storage import read_json, write_json

    asset = get_asset(asset_id)
    if not asset:
        raise NotFound(f"The show kit has no item called {asset_id!r}.")
    src = source_path(asset, drive)
    if src is None:
        raise CutzoError(f"{asset['name']} isn't a sound file Cutzo can use.")
    copy, info_path = kit_dir / f"{asset_id}.f32", kit_dir / f"{asset_id}.json"
    try:
        stat = src.stat()
    except OSError:
        if copy.exists():
            return copy  # Drive is offline: the copy from last time still works
        raise NotFound(f"Can't find {asset['name']} in Google Drive ({src}).",
                       hint="Check that Google Drive is running and the file is still in that folder.") from None
    info = read_json(info_path)
    fresh = isinstance(info, dict) and info.get("size") == stat.st_size and info.get("mtime") == int(stat.st_mtime)
    if copy.exists() and fresh:
        return copy
    kit_dir.mkdir(parents=True, exist_ok=True)
    tmp = kit_dir / f"{asset_id}.part.f32"
    ffmpeg.decode(src, tmp, channels=2, check=check)
    tmp.replace(copy)
    write_json(info_path, {"source": str(src), "size": stat.st_size, "mtime": int(stat.st_mtime),
                           "channels": 2, "rate": 48000})
    return copy


def seconds(asset_id: str) -> float | None:
    """How long a kit item is (recorded in the inventory), without reading the file."""
    asset = get_asset(asset_id)
    return float(asset["seconds"]) if asset and asset.get("seconds") else None


def loudness(asset_id: str, *, kit_dir: Path, drive: Path | None, check=None) -> float:
    """Integrated loudness (LUFS) of a kit item's sound, measured once on its working copy."""
    from ..tools import ffmpeg
    from .storage import read_json, write_json

    copy = local_copy(asset_id, kit_dir=kit_dir, drive=drive, check=check)
    info_path = kit_dir / f"{asset_id}.json"
    info = read_json(info_path)
    info = info if isinstance(info, dict) else {}
    if info.get("lufs") is None:
        info["lufs"] = ffmpeg.loudness(copy, channels=2, check=check)["integrated"]
        write_json(info_path, info)
    return float(info["lufs"])


VIDEO_SIZE = (1920, 1080)


def video_copy(asset_id: str, *, kit_dir: Path, drive: Path | None, until: float | None = None,
               check=None, progress=None) -> Path:
    """A 1080p, 29.97 fps copy of a kit video (no sound), so making an episode's video never has to
    decode Nick's 4K files. Made the first time it's needed (covering at least `until` seconds from
    the start, or the whole file), and again when the file in Drive changes."""
    from ..tools import ffmpeg
    from .errors import CutzoError, NotFound
    from .storage import read_json, write_json

    asset = get_asset(asset_id)
    if not asset:
        raise NotFound(f"The show kit has no item called {asset_id!r}.")
    src = source_path(asset, drive)
    if src is None or asset.get("kind") != "video":
        raise CutzoError(f"{asset['name']} isn't a video Cutzo can use.")
    out_dir = kit_dir / "video"
    dst, info_path = out_dir / f"{asset_id}-1080p.mp4", out_dir / f"{asset_id}-1080p.json"
    info = read_json(info_path)
    info = info if isinstance(info, dict) else {}
    try:
        stat = src.stat()
    except OSError:
        if dst.exists():
            return dst  # Drive is offline: the copy from last time still works
        raise NotFound(f"Can't find {asset['name']} in Google Drive ({src}).",
                       hint="Check that Google Drive is running and the file is still in that folder.") from None
    same = info.get("size") == stat.st_size and info.get("mtime") == int(stat.st_mtime)
    enough = info.get("whole") or (until is not None and float(info.get("seconds") or 0) >= until)
    if dst.exists() and same and enough:
        return dst
    length = ffmpeg.probe(src).get("duration") or 0.0
    whole = until is None or until >= length - 1.0
    span = length if whole else min(length, max(until + 60.0, 300.0))
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f"{asset_id}-1080p.part.mp4"
    w, h = VIDEO_SIZE
    # Decoded on the processor: on Nick's PC (Ryzen 9 3900X, RTX 2070 SUPER) "-hwaccel auto" made the
    # 4K background copy twice as slow, because every 4K frame then has to come back from the
    # graphics card (measured Oct 15, 2026: 20 s of BG Loop in 5.1 s on the processor, 10.4 s with it).
    args = ["-i", str(src)]
    if not whole:
        args += ["-t", f"{span:.3f}"]
    args += ["-an", "-sn", "-dn", "-vf", f"scale={w}:{h}:flags=lanczos,fps=30000/1001,format=yuv420p,setsar=1",
             "-c:v", "libx264", "-preset", "faster", "-crf", "16", "-g", "30", "-keyint_min", "30",
             "-sc_threshold", "0", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
             "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    try:
        ffmpeg.run(args, duration=span, progress=progress, check=check)
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    write_json(info_path, {"source": str(src), "size": stat.st_size, "mtime": int(stat.st_mtime),
                           "seconds": round(span, 3), "whole": whole, "width": w, "height": h})
    return dst
