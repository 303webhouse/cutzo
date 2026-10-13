"""Nick's show kit: the backgrounds, intros, sounds and art each show uses.

The files live in Google Drive (mounted as G: on Nick's PC). In Phase 1 Cutzo
only lists them. Phase 2 (The Dig) and Phase 3 (The New Heretics) copy what each
show uses into F:\\Cutzo\\kit so renders never depend on Drive syncing.
Inventory taken from Drive on Oct 13, 2026.
"""

from __future__ import annotations

from typing import Any

TNH = "The New Heretics"
DIG = "Denver Stiffs/The Dig"


def _a(asset_id: str, show: str, name: str, folder: str, kind: str, use: str,
       drive_id: str | None = None, note: str | None = None, size: str | None = None) -> dict[str, Any]:
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
        "location": "builtin" if kind == "style" else "drive",
    }


ASSETS: list[dict[str, Any]] = [
    # --- The New Heretics: backgrounds ----------------------------------
    _a("tnh-bg-loop-1hr", "the-new-heretics", "BG Loop (1 hr).mp4", f"{TNH}/YouTube BG/YouTube Files (FINAL)",
       "video", "Background loop", "1G9b6bkiH4SSR1HbP1YwsT6-eCxrgJb8G", "One hour long, so no looping needed."),
    _a("tnh-fire-loop-1min", "the-new-heretics", "YT FIRE LOOP (1 MIN).mp4", f"{TNH}/YouTube BG/BG Animation",
       "video", "Background loop", "13ut6tFk2AlH5FBZ701YhpFONq7kngRc-", "Loops seamlessly for any length."),
    _a("tnh-fire-loop", "the-new-heretics", "YT Fire Loop.mp4", f"{TNH}/YouTube BG/BG Animation",
       "video", "Background loop", "13h_D_bZPsL4grmI8v7Bpt00eccvH9nN4"),
    _a("tnh-bg-master", "the-new-heretics", "YT BG Master (Final).mp4", f"{TNH}/YouTube BG/BG Animation",
       "video", "Background loop", "13aA3yBqfJS73Tj5rujK999RmF3uP8hxq"),
    _a("tnh-full-flames", "the-new-heretics", "Full Flames (Final).mp4", f"{TNH}/YouTube BG/YouTube Files (FINAL)",
       "video", "Background loop", "1GzPsEAtTapwjb4-7T2YUE-Aw02-EuFSo"),
    _a("tnh-streaking-fire", "the-new-heretics", "Streaking Fire (4K 30p).mp4", f"{TNH}/YouTube BG/YouTube Files (FINAL)",
       "video", "Background loop", "1CAzA4dOLapj8_5R2hwvcZey3UgEVcVyk"),
    _a("tnh-streaking-logos-nobg", "the-new-heretics", "Streaking with Logos NO BG (4K30p).mp4",
       f"{TNH}/YouTube BG/YouTube Files (FINAL)", "video", "Logo overlay", "1EBnRui_sFksV8O-v7UNOv0aUUuIvJzLZ",
       "On black; blended over the background so the black disappears."),
    # --- The New Heretics: intro, music, sounds, art -------------------
    _a("tnh-intro", "the-new-heretics", "YT Intro (Final).mp4", f"{TNH}/YouTube BG/Intro",
       "video", "Opening montage", "17DXsBBqHR5kktI7Kg_hXAl-7-W92MY-f"),
    _a("tnh-title-sequence", "the-new-heretics", "YT Title Sequence.mp4", f"{TNH}/YouTube BG/Intro",
       "video", "Opening montage", "16YxGz4Hm7yh_4EwAGDq5Ms2pbuURm1Wj"),
    _a("tnh-music-loop", "the-new-heretics", "Music Loop (5 mins).mp4", f"{TNH}/YouTube BG/YouTube Files (FINAL)",
       "video", "Music bed", "1FZK8g5ObRG_j2gXhkTa2rXLPWJ29UNkR", "Only its sound is used, turned down under speech."),
    _a("tnh-seg-the-new-heretics", "the-new-heretics", "The New Heretics.mp3", f"{TNH}/Podcast/Subtitles",
       "audio", "Voiced segment title", "1XLRb_BEKUK4Eh7YfFi-P4DD5EtIgy3wu"),
    _a("tnh-seg-define-your-terms", "the-new-heretics", "Define Your Terms.mp3", f"{TNH}/Podcast/Subtitles",
       "audio", "Voiced segment title", "1XNOllIoA_fA-WDrEY_2jRqZKPNkcIW-O"),
    _a("tnh-seg-why-should-we-care", "the-new-heretics", "Why Should We Care.mp3", f"{TNH}/Podcast/Subtitles",
       "audio", "Voiced segment title", "1XNs9qZXGOXIWO9sIHxHFZHbtNarEsRlW"),
    _a("tnh-seg-building-a-better-apostasy", "the-new-heretics", "Building a Better Apostasy.mp3",
       f"{TNH}/Podcast/Subtitles", "audio", "Voiced segment title", "1XMtyRlF6bv-Jdjgyk8DQ1U0QX5o8cvU5"),
    _a("tnh-seg-heretical-haiku", "the-new-heretics", "Heretical Haiku.mp3", f"{TNH}/Podcast/Subtitles",
       "audio", "Voiced segment title", "1XKNjyf0sjRc3qVMwApyC8KJW4CxH593u"),
    _a("tnh-seg-logo-loses-us", "the-new-heretics", "Logo Loses Us.mp3", f"{TNH}/Podcast/Subtitles",
       "audio", "Voiced segment title", "1XL7pF-tQZTRh49Nl2s0OMD3ldgLB_2nO"),
    _a("tnh-turn-the-page", "the-new-heretics", "Turn the Page.wav", f"{TNH}/Podcast/Subtitles",
       "audio", "Transition sound", "1Xt_oDh-55v7xczdKg-Snia0biWO2ckuX"),
    _a("tnh-page-flip", "the-new-heretics", "Page Flip.wav", f"{TNH}/Podcast/Sounds", "audio", "Transition sound"),
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
    _a("dig-intro-gabe", "the-dig", "Gabe Audio.mp3", f"{DIG}/Intro", "audio", "Intro clip"),
    _a("dig-intro-doug-moe", "the-dig", "Doug Moe Greatness.wav", f"{DIG}/Intro", "audio", "Intro clip"),
    _a("dig-intro-take-that-l", "the-dig", "Take that l on the way out.wav", f"{DIG}/Intro", "audio", "Intro clip"),
    _a("dig-intro-not-meant", "the-dig", "Not meant for this game.wav", f"{DIG}/Intro", "audio", "Intro clip"),
    _a("dig-sfx-three-digs", "the-dig", "Three Digs.wav", f"{DIG}/Sound Effects", "audio", "Sound effect"),
    _a("dig-sfx-twolves-win", "the-dig", "Twolves Win.wav", f"{DIG}/Sound Effects", "audio", "Sound effect"),
    _a("dig-sfx-national-anthem", "the-dig", "National Anthem.wav", f"{DIG}/Sound Effects", "audio", "Sound effect"),
    _a("dig-sfx-tom-haiku-intro", "the-dig", "Tom Haiku Intro.wav", f"{DIG}/Sound Effects", "audio", "Sound effect"),
    _a("dig-sfx-mining-chops", "the-dig", "Mining Chops.wav", f"{DIG}/Sound Effects", "audio", "Sound effect"),
    _a("dig-sfx-untitled", "the-dig", "untitled.wav", f"{DIG}/Sound Effects", "audio", "Sound effect"),
    _a("dig-jok-banner", "the-dig", "Jok Banner.jpg", DIG, "image", "Cover art"),
    _a("dig-final-example", "the-dig", "2025-26 Season Debrief Part 2 -- Impossible Trades.mp3",
       f"{DIG}/Season Debrief Part 2", "audio", "Finished example to match", "1nVwqJ3ffGcCpchUUk7Dz-unHaND3qhH3",
       "Cutzo's version of this episode is compared against it by ear."),
    # --- Built into Cutzo --------------------------------------------------
    _a("spectrum-ghost-bars", "*", "Ghost Stories bars", "Built into Cutzo", "style", "Audio spectrum",
       note="Bars styled to match JW Ghost Stories.mp4 (tuned in Phase 3)."),
    _a("spectrum-wave-line", "*", "Thin wave line", "Built into Cutzo", "style", "Audio spectrum"),
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
