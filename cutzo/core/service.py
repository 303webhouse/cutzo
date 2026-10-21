"""Cutzo's core: every action the app or Claude can take, as plain methods.

The web engine (cutzo.engine) and the Claude connector (cutzo.mcp_server) are
thin wrappers around this class, so the screens and Claude can never drift apart.

Sample episodes (Phase 1) still pretend the heavy media work with timed steps.
Real episodes (Phase 2 on) are built for real: FFmpeg reads and lines up the
recordings, DeepFilterNet and FFmpeg clean the voices, and dead air is found in
the cleaned audio (see build.py and voice.py). Transcripts arrive in Phase 3.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import threading
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .. import PHASE, __version__
from .. import tools as toolbox
from ..tools import ffmpeg
from . import audio, cutpoints, kit, presets, recipes, render, sample, timeline, titles
from . import captions as captionslib
from . import video as videolib
from . import build as buildlib
from . import shows as showlib
from . import teaser as teaserlib
from . import transcript as tx
from .activity import ActivityLog
from .errors import CutzoError, NeedsConfirmation, NotAllowed, NotFound
from .jobs import JobContext, JobRunner, now_iso
from .media import classify, kind_of, scan_folder
from .safety import Guard
from .storage import read_json, write_json
from .timecode import fmt_duration, fmt_time, parse_time
from .voice import VoiceSettings

log = logging.getLogger("cutzo")

STAGES = [
    ("import", "Import"),
    ("sync", "Sync"),
    ("voice", "Podcast voice"),
    ("transcribe", "Transcribe"),
    ("auto_edit", "Auto-edit"),
    ("review", "Review"),
    ("export", "Export"),
]
STAGE_LABELS = {**dict(STAGES), "done": "Exported"}
BUILD_STEPS = [("sync", "Sync", 2.0), ("voice", "Podcast voice", 3.0),
               ("transcribe", "Transcribe", 4.0), ("auto_edit", "Auto-edit", 2.5)]
STAGE_DETAIL = {
    "sync": "Lining up everyone's recordings",
    "voice": "Cleaning noise and echo, evening out voices",
    "transcribe": "Writing down every word with its time",
    "auto_edit": "Finding dead air and filler words",
}
VOICE_CHAIN = ["Rumble filter (80 Hz)", "Noise removal", "Room echo removal", "Voice enhancement",
               "De-essing", "Gentle compression", "Leveling"]
BIG_CUT_SECONDS = 600.0
BIG_CUT_SHARE = 0.25
PHASE1_NOTE = "Sample episode: pretend data, nothing is rendered and your real files are never touched."
SAMPLE_EXPORT_NOTE = "Sample episode: the export is pretend, so no file is written."
SAMPLE_BUILD_NOTE = "Sample episode: the steps are pretend and use a short test clip instead of a real recording."
REAL_NOTE = ("Real episode: Cutzo reads your recordings (never changes them), cleans the voices, writes down every "
             "word and finds dead air.")
REAL_BUILD_NOTE = ("Lines up the recordings, cleans the voices, writes down every word and finds dead air. "
                   "A 90-minute episode takes about 15 minutes.")
ENGINE_NOTE = "Phase 3: transcripts for real episodes and The New Heretics' video at 1080p. Sample episodes stay pretend."
NO_TRANSCRIPT = ("This episode has no transcript yet. Transcribe it (it takes a few minutes), then cut by words. "
                 "Until then, cut by time.")
ANGLE_ALIASES = {"everyone", "all", "wide", "grid", "group", "everybody"}


def slugify(text: str, limit: int = 70) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug[:limit].rstrip("-")) or "episode"


def safe_filename(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text).strip().rstrip(".")
    return cleaned[:120] or "episode"


def _q(text: str, limit: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


class Cutzo:
    def __init__(self, library: Path | str, *, speed: float = 1.0, seed_samples: bool = True, workers: int = 2,
                 drive: Path | str | None = None):
        self.library = Path(library)
        self.drive = Path(drive) if drive else None  # Google Drive, where the show kit lives
        self.library.mkdir(parents=True, exist_ok=True)
        for sub in ("episodes", "exports", "kit", "jobs", "logs", "samples", "trash"):
            (self.library / sub).mkdir(exist_ok=True)
        self.guard = Guard(self.library)
        self.activity = ActivityLog(self.library / "activity.jsonl")
        self.jobs = JobRunner(self.library / "jobs", workers=workers, speed=speed, on_finish=self._job_finished)
        self._lock = threading.RLock()
        self.started = now_iso()
        if seed_samples and not (self.library / "samples" / "seeded.json").exists():
            self.reset_samples(source="cutzo")

    # ================================================================ info
    def info(self) -> dict[str, Any]:
        try:
            usage = shutil.disk_usage(self.library)
            free_gb = round(usage.free / 1e9, 1)
        except OSError:
            free_gb = None
        return {
            "app": "cutzo",
            "version": __version__,
            "phase": PHASE,
            "library": str(self.library),
            "free_gb": free_gb,
            "python": platform.python_version(),
            "platform": platform.platform(terse=True),
            "pid": os.getpid(),
            "started": self.started,
            "note": ENGINE_NOTE,
        }

    # =============================================================== shows
    def list_shows(self, source: str = "you") -> list[dict[str, Any]]:
        self._log_read(source, "list_shows", "looked at the shows")
        counts: dict[str, int] = {}
        for ep in self._all_projects():
            counts[ep["show"]] = counts.get(ep["show"], 0) + 1
        out = []
        for show in showlib.list_shows():
            out.append(self._show_view(show, counts.get(show["id"], 0)))
        return out

    def get_show(self, show: str, source: str = "you") -> dict[str, Any]:
        found = showlib.find_show(show)
        if not found:
            raise NotFound(f"No show called \u201c{show}\u201d.", hint="Shows: The Dig, The New Heretics.")
        self._log_read(source, "get_show", f"looked at {found['name']}")
        count = sum(1 for ep in self._all_projects() if ep["show"] == found["id"])
        return self._show_view(found, count)

    def _show_view(self, show: dict[str, Any], episode_count: int) -> dict[str, Any]:
        view = dict(show)
        view["episode_count"] = episode_count
        view["slots"] = [self._slot_view(show, s, None) for s in show.get("slots", [])]
        view["outputs"] = [presets.get_preset(p) for p in show.get("outputs", [])]
        view["segment_titles"] = [kit.get_asset(a) for a in show.get("segment_titles", [])]
        return view

    def _slot_view(self, show: dict[str, Any], slot: dict[str, Any], overrides: dict[str, str] | None) -> dict[str, Any]:
        chosen = (overrides or {}).get(slot["slot"], slot["default"])
        return {
            "slot": slot["slot"],
            "label": slot["label"],
            "note": slot.get("note"),
            "default": slot["default"],
            "overridden": chosen != slot["default"],
            "asset": kit.get_asset(chosen),
            "choices": [kit.get_asset(c) for c in slot.get("choices", [])],
        }

    # ================================================================ kit
    def list_assets(self, show: str | None = None, source: str = "you") -> dict[str, Any]:
        show_id = None
        if show:
            found = showlib.find_show(show)
            if not found:
                raise NotFound(f"No show called \u201c{show}\u201d.", hint="Shows: The Dig, The New Heretics.")
            show_id = found["id"]
        self._log_read(source, "list_assets", "looked at the show kit")
        used: dict[str, list[str]] = {}
        for s in showlib.list_shows():
            for slot in s.get("slots", []):
                used.setdefault(slot["default"], []).append(f"{s['name']}: {slot['label']}")
            for a in s.get("segment_titles", []):
                used.setdefault(a, []).append(f"{s['name']}: voiced segment title")
        assets = []
        for a in kit.list_assets(show_id):
            a["used_by"] = used.get(a["id"], [])
            a["drive_url"] = kit.drive_url(a)
            assets.append(a)
        return {
            "assets": assets,
            "kit_folder": str(self.library / "kit"),
            "note": "The kit lives in Google Drive. Phase 2 and 3 copy what each show uses into "
                    f"{self.library / 'kit'} so renders never wait on Drive.",
        }

    # ============================================================ episodes
    def list_episodes(self, show: str | None = None, source: str = "you") -> list[dict[str, Any]]:
        show_id = None
        if show:
            found = showlib.find_show(show)
            if not found:
                raise NotFound(f"No show called \u201c{show}\u201d.", hint="Shows: The Dig, The New Heretics.")
            show_id = found["id"]
        self._log_read(source, "list_episodes", "looked at the episode list")
        eps = [self._summary(ep) for ep in self._all_projects() if not show_id or ep["show"] == show_id]
        eps.sort(key=lambda e: e["updated"], reverse=True)
        return eps

    def get_episode(self, episode: str, source: str = "you") -> dict[str, Any]:
        ep = self._load(self.resolve_episode(episode))
        self._log_read(source, "get_episode", f"looked at {_q(ep['title'], 40)}", ep["id"])
        return self._view(ep)

    def resolve_episode(self, query: str) -> str:
        q = str(query or "").strip()
        if not q:
            raise CutzoError("Which episode? Give its name or id.", hint=self._episode_hint())
        projects = self._all_projects()
        ids = {ep["id"] for ep in projects}
        if q in ids:
            return q
        ql = q.lower()
        for test in (
            lambda ep: ep["title"].lower() == ql,
            lambda ep: ql in ep["title"].lower() or ql in ep["id"],
            lambda ep: all(t in tx.tokens(ep["title"] + " " + ep["id"].replace("-", " ")) for t in tx.tokens(q)),
        ):
            hits = [ep for ep in projects if test(ep)]
            if len(hits) == 1:
                return hits[0]["id"]
            if len(hits) > 1:
                names = "; ".join(f"{e['title']} ({e['id']})" for e in hits[:6])
                raise CutzoError(f"\u201c{q}\u201d matches {len(hits)} episodes: {names}. Which one?",
                                 data={"matches": [e["id"] for e in hits]})
        raise NotFound(f"No episode matches \u201c{q}\u201d.", hint=self._episode_hint())

    def create_episode(self, show: str | None = None, title: str | None = None, folder: str | None = None,
                       source: str = "you") -> dict[str, Any]:
        folder_path = None
        if folder:
            folder_path = Path(str(folder).strip().strip('"'))
            if not folder_path.is_dir():
                raise NotFound(f"Can't find the folder {folder_path}.",
                               hint="Copy the folder's path from File Explorer's address bar, like F:\\Recordings\\Episode 12.")
        show_obj = showlib.find_show(show) if show else self._guess_show(folder_path)
        if not show_obj:
            if show:
                raise NotFound(f"No show called \u201c{show}\u201d.", hint="Shows: The Dig, The New Heretics.")
            raise CutzoError("Which show is this episode for: The Dig or The New Heretics?")
        title = " ".join((title or (folder_path.name if folder_path else "")).split())
        if not title:
            raise CutzoError("Give the episode a title.")
        if len(title) > 120:
            raise CutzoError("That title is longer than 120 characters; please shorten it.")
        with self._lock:
            ep_id = self._unique_id(slugify(f"{show_obj['id']}-{title}"))
            ep = self._new_project(ep_id, show_obj, title, folder=str(folder_path) if folder_path else None)
            self._save(ep)
        self._log(source, "create_episode", f"Created episode \u201c{title}\u201d for {show_obj['name']}", ep_id)
        result: dict[str, Any] = {"episode": self._summary(ep)}
        if folder_path:
            found = scan_folder(folder_path)
            if found:
                result["import"] = self.import_media(ep_id, [f["path"] for f in found], source=source)
            else:
                result["note"] = "No recordings found in that folder yet. Add them with Import."
        return result

    def remove_episode(self, episode: str, source: str = "you") -> dict[str, Any]:
        ep_id = self.resolve_episode(episode)
        if self.jobs.active_for(ep_id):
            raise CutzoError("This episode has a job running. Wait for it or cancel it first.")
        ep = self._load(ep_id)
        dest = self._trash(ep_id)
        self._log(source, "remove_episode",
                  f"Removed \u201c{ep['title']}\u201d from Cutzo (moved to the trash folder; recordings untouched)", ep_id)
        return {"removed": ep_id, "moved_to": str(dest)}

    # ============================================================== import
    def import_media(self, episode: str, paths: Iterable[str] | str, source: str = "you") -> dict[str, Any]:
        ep_id = self.resolve_episode(episode)
        if isinstance(paths, str):
            paths = [p for p in re.split(r"[\r\n]+", paths) if p.strip()]
        paths = [str(p).strip().strip('"') for p in (paths or []) if str(p).strip()]
        if not paths:
            raise CutzoError("No files given. Paste the paths of the recordings or their folder.")
        files: list[dict[str, Any]] = []
        missing: list[str] = []
        skipped: list[str] = []
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                files.extend(scan_folder(p))
            elif p.is_file():
                if kind_of(p) in ("audio", "video"):
                    info = classify(p)
                    info["bytes"] = p.stat().st_size
                    files.append(info)
                else:
                    skipped.append(p.name)
            else:
                missing.append(str(p))
        if missing:
            shown = "; ".join(missing[:5])
            raise NotFound(f"Can't find {len(missing)} of those files: {shown}",
                           hint="Check the paths. On your PC they look like F:\\Recordings\\Episode 12\\file.wav.",
                           data={"missing": missing})
        if not files:
            raise CutzoError("No audio or video files found there.", data={"skipped": skipped})
        title = self._load(ep_id)["title"]
        with self._lock:
            self._refuse_if_busy(ep_id, "import")
            job = self.jobs.submit(kind="import", title=f"Import recordings for {title}", episode=ep_id, source=source,
                                   steps=["Checking files", "Reading recording details"],
                                   work=self._import_work(ep_id, files))
        self._log(source, "import_media", f"Started importing {len(files)} recording(s) into \u201c{title}\u201d",
                  ep_id, details={"job": job["id"], "files": [f["name"] for f in files][:20]})
        return {"job": job, "files": [f["name"] for f in files], "skipped": skipped}

    def set_recording_order(self, episode: str, files: Iterable[str] | str, source: str = "you") -> dict[str, Any]:
        """The playing order of combined recordings that follow one another (the recording was stopped and
        started again). `files` names them first to last: ids, file names or part of a name."""
        if isinstance(files, str):
            files = [f for f in re.split(r"[\r\n,;]+", files) if f.strip()]
        wanted = [str(f).strip().strip('"') for f in files or [] if str(f).strip()]
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            mixes = [m for m in ep.get("media", []) if buildlib.is_mix(m) and not m.get("problem")]
            if len(mixes) < 2:
                raise CutzoError("This episode has only one combined recording, so there's no order to set.")
            chosen: list[dict[str, Any]] = []
            for w in wanted:
                low = w.lower()
                hits = [m for m in mixes if m["id"] == w or m["name"].lower() == low or m.get("path", "").lower() == low]
                hits = hits or [m for m in mixes if low in m["name"].lower()]
                if len(hits) != 1:
                    names = "; ".join(m["name"] for m in mixes)
                    raise CutzoError(f"\u201c{w}\u201d {'matches several recordings' if hits else 'matches none of the recordings'}.",
                                     hint=f"Name them by file name: {names}")
                if hits[0] in chosen:
                    raise CutzoError(f"{hits[0]['name']} is named twice.")
                chosen.append(hits[0])
            if not chosen:
                raise CutzoError("Name the recordings in playing order, first one first.")
            order = chosen + [m for m in buildlib.join_order(mixes) if m not in chosen]
            before = {m["id"]: m.get("part") for m in mixes}
            for k, m in enumerate(order):
                m["part"] = k + 1
            changed = any(before[m["id"]] != m["part"] for m in order)
            summary = "Recordings play in this order: " + ", then ".join(m["name"] for m in order)
            if changed:
                if ep["stages"].get("sync") == "done":
                    ep["needs_rebuild"] = True
                    summary += " (build again to hear it)"
                self._push_history(ep, {"op": "media_order", "before": before, "summary": summary})
            self._save(ep)
        self._log(source, "set_recording_order", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        return {"summary": summary, "order": [m["name"] for m in order], "needs_rebuild": bool(ep.get("needs_rebuild"))}

    def scan(self, folder: str) -> dict[str, Any]:
        """Preview what a folder contains (read-only), for the New episode form."""
        path = Path(str(folder).strip().strip('"'))
        if not path.is_dir():
            raise NotFound(f"Can't find the folder {path}.")
        found = scan_folder(path)
        guess = self._guess_show(path)
        return {"folder": str(path), "files": found, "show_guess": guess["id"] if guess else None}

    def _import_work(self, ep_id: str, files: list[dict[str, Any]]):
        def work(ctx: JobContext) -> dict[str, Any]:
            ctx.step("Checking files", 0.05)
            probes: dict[str, dict[str, Any]] = {}
            if self._load(ep_id).get("sample"):
                ctx.sleep(1.0)
                ctx.step("Reading recording details", 0.5)
                ctx.sleep(1.0)
            else:
                for k, info in enumerate(files):
                    ctx.step("Reading recording details", 0.1 + 0.85 * k / max(1, len(files)), info["name"])
                    try:
                        probes[info["path"]] = ffmpeg.probe(info["path"])
                    except CutzoError as exc:
                        probes[info["path"]] = {"problem": exc.message}
            problems = []
            with self._lock:
                ep = self._load(ep_id)
                known = {m["path"].lower() for m in ep["media"]}
                added = 0
                for info in files:
                    if info["path"].lower() in known:
                        continue
                    entry = dict(info)
                    entry["id"] = f"m{len(ep['media']) + 1}"
                    entry["original"] = True
                    entry["exists"] = True
                    found = probes.get(info["path"])
                    if found is not None:
                        if found.get("problem"):
                            entry["problem"] = found["problem"]
                            problems.append(found["problem"])
                        else:
                            entry["probe"] = found
                            entry["duration"] = found.get("duration")
                    ep["media"].append(entry)
                    known.add(info["path"].lower())
                    added += 1
                ep["cameras"] = self._cameras(ep["media"])
                ep["stages"]["import"] = "done"
                if added and ep["stages"].get("auto_edit") == "done":
                    ep["needs_rebuild"] = True
                self._save(ep)
            summary = f"Imported {added} recording(s) into \u201c{ep['title']}\u201d."
            if problems:
                summary += f" {len(problems)} couldn't be read: {problems[0]}"
            return {"added": added, "summary": summary, "problems": problems}
        return work

    # =============================================================== build
    def build_episode(self, episode: str, template: str | None = None, source: str = "you") -> dict[str, Any]:
        ep_id = self.resolve_episode(episode)
        ep = self._load(ep_id)
        if not ep["media"]:
            raise CutzoError("Import the recordings first, then build.", hint="Use Import (or import_media) with the files or their folder.")
        show = showlib.find_show(template) if template else showlib.get_show(ep["show"])
        if not show:
            raise NotFound(f"No show template called \u201c{template}\u201d.", hint="Templates: The Dig, The New Heretics.")
        if not ep.get("sample"):
            usable = [m for m in ep["media"] if not m.get("problem")]
            if not usable:
                raise CutzoError("None of this episode's recordings could be read, so there's nothing to build.",
                                 hint="Import the recordings again, or check that they open on this computer.")
            toolbox.require("ffmpeg")
            if VoiceSettings.from_dict(self._voice_settings(ep, show)).strength:
                toolbox.require("deep-filter")
        with self._lock:
            self._refuse_if_busy(ep_id, "build")
            job = self.jobs.submit(kind="build", title=f"Build {ep['title']}", episode=ep_id, source=source,
                                   steps=[label for _, label, _ in BUILD_STEPS], work=self._build_work(ep_id, show["id"]))
        self._log(source, "build_episode", f"Started building \u201c{ep['title']}\u201d (template: {show['name']})",
                  ep_id, details={"job": job["id"]})
        return {"job": job, "steps": [label for _, label, _ in BUILD_STEPS],
                "note": SAMPLE_BUILD_NOTE if ep.get("sample") else REAL_BUILD_NOTE}

    def _build_work(self, ep_id: str, show_id: str):
        def work(ctx: JobContext) -> dict[str, Any]:
            if self._load(ep_id).get("sample"):
                return pretend(ctx)
            return self._real_build(ctx, ep_id, show_id)

        def pretend(ctx: JobContext) -> dict[str, Any]:
            total = sum(secs for _, _, secs in BUILD_STEPS)
            elapsed = 0.0
            current = None
            try:
                for key, label, secs in BUILD_STEPS:
                    current = key
                    self._set_stage(ep_id, key, "running")
                    ctx.step(label, elapsed / total, STAGE_DETAIL[key])
                    slices = 5
                    for k in range(slices):
                        ctx.sleep(secs / slices)
                        ctx.step(label, (elapsed + secs * (k + 1) / slices) / total, STAGE_DETAIL[key])
                    if key == "sync":
                        self._apply_sync(ep_id)
                    elif key == "voice":
                        self._apply_voice(ep_id, show_id)
                    elif key == "transcribe":
                        self._apply_transcribe(ep_id)
                    elif key == "auto_edit":
                        self._apply_auto_edit(ep_id)
                    self._set_stage(ep_id, key, "done")
                    elapsed += secs
                current = None
            finally:
                if current:
                    self._set_stage(ep_id, current, "todo")
            ep = self._load(ep_id)
            cuts = [e for e in ep["edits"] if e["type"] == "cut" and e.get("by") == "auto"]
            return {
                "summary": f"Built \u201c{ep['title']}\u201d: {len(cuts)} suggested cuts and "
                           f"{len(ep['chapters'])} chapters. Ready to review.",
                "suggested_cuts": len(cuts),
                "chapters": len(ep["chapters"]),
            }
        return work

    def _apply_sync(self, ep_id: str) -> None:
        with self._lock:
            ep = self._load(ep_id)
            rnd = zlib.crc32(ep_id.encode())
            many = len(ep["media"]) > 1
            for n, m in enumerate(ep["media"]):
                base = m.get("offset")
                if base is not None:
                    jitter = (((rnd >> n) % 31) - 15) / 1000.0
                    m["sync"] = {"offset": round(base + jitter, 3), "method": "From the file name, then matched by sound"}
                elif many:
                    m["sync"] = {"offset": 0.0, "method": "Matched by sound"}
                else:
                    m["sync"] = {"offset": 0.0, "method": "Single recording, nothing to line up"}
            self._save(ep)

    def _apply_voice(self, ep_id: str, show_id: str) -> None:
        show = showlib.get_show(show_id) or {}
        voice = show.get("voice", {"preset": "Podcast voice", "strength": 25})
        with self._lock:
            ep = self._load(ep_id)
            ep["voice"] = {"preset": voice["preset"], "strength": voice["strength"], "steps": VOICE_CHAIN,
                           "note": "Phase 1 shows the settings only. Phase 2 makes the cleanup real."}
            self._save(ep)

    def _apply_transcribe(self, ep_id: str) -> None:
        ep = self._load(ep_id)
        script_name = ep.get("script") or "generic"
        names = None
        if script_name == "generic":
            people = []
            for m in ep["media"]:
                if m.get("person") and m["person"] not in people:
                    people.append(m["person"])
            names = people or None
        performance = sample.perform(sample.script_for(script_name, names), seed=zlib.crc32(ep_id.encode()) % 1000)
        ep_dir = self._ep_dir(ep_id)
        wav = self.guard.writable(ep_dir / "preview.wav")
        sample.synthesize(performance, wav, seed=zlib.crc32(ep_id.encode()) % 997)
        write_json(self.guard.writable(ep_dir / "transcript.json"), {
            "speakers": performance["speakers"],
            "words": performance["words"],
            "sample": True,
            "made": now_iso(),
        })
        cache = ep_dir / "waveform.json"
        if cache.exists():
            cache.unlink()
        with self._lock:
            ep = self._load(ep_id)
            ep["duration"] = performance["duration"]
            ep["analysis"] = {"pauses": performance["pauses"], "suggested_chapters": performance["chapters"]}
            self._save(ep)

    def _apply_auto_edit(self, ep_id: str) -> None:
        with self._lock:
            ep = self._load(ep_id)
            words = self._transcript(ep_id).get("words", [])
            found = sample.auto_edits({"pauses": ep.get("analysis", {}).get("pauses", []), "words": words})
            ep["edits"] = [e for e in ep["edits"] if e.get("by") != "auto"]
            for e in found:
                ep["edits"].append(self._new_edit(ep, by="auto", **e))
            if ep.get("cameras"):
                # Follow whoever is talking; hold the shot through pauses until the next person speaks.
                paras = tx.paragraphs({"speakers": self._transcript(ep_id).get("speakers", []), "words": words})
                for k, para in enumerate(paras):
                    angle = para["speaker"] if para["speaker"] in ep["cameras"] else "Everyone"
                    if angle not in ep["cameras"]:
                        angle = ep["cameras"][0]
                    start = 0.0 if k == 0 else para["start"]
                    end = paras[k + 1]["start"] if k + 1 < len(paras) else float(ep["duration"])
                    ep["edits"].append(self._new_edit(ep, type="camera", start=round(start, 3), end=round(end, 3),
                                                      angle=angle, by="auto", reason="follows the speaker"))
            ep["chapters"] = [c for c in ep["chapters"] if c.get("by") != "auto"]
            taken = [c["time"] for c in ep["chapters"]]
            for ch in ep.get("analysis", {}).get("suggested_chapters", []):
                if all(abs(ch["time"] - t) > 1.0 for t in taken):
                    ep["chapters"].append(self._new_chapter(ep, ch["time"], ch["title"], by="auto"))
            ep["chapters"].sort(key=lambda c: c["time"])
            ep.pop("needs_rebuild", None)
            self._save(ep)

    # ---------------------------------------------------------- real build
    def _voice_settings(self, ep: dict[str, Any], show: dict[str, Any] | None = None) -> dict[str, Any]:
        show = show or showlib.get_show(ep["show"]) or {}
        return {**(show.get("voice") or {}), **(ep.get("voice_settings") or {})}

    def _real_build(self, ctx: JobContext, ep_id: str, show_id: str) -> dict[str, Any]:
        """Phase 2: line up the recordings, clean the voices, find dead air (see build.py)."""
        ep = self._load(ep_id)
        show = showlib.get_show(show_id) or {}
        settings = VoiceSettings.from_dict(self._voice_settings(ep, show))
        ep_dir = self._ep_dir(ep_id)
        work = self.guard.writable(ep_dir / "work")
        transcribe = toolbox.available("parakeet-cli", "parakeet-model")
        share = ({"sync": (0.0, 0.08), "voice": (0.08, 0.72), "transcribe": (0.80, 0.14), "auto_edit": (0.94, 0.06)}
                 if transcribe else {"sync": (0.0, 0.12), "voice": (0.12, 0.82), "auto_edit": (0.94, 0.06)})

        def reporter(key: str, label: str):
            base, size = share[key]
            return lambda p, detail=None: ctx.step(label, base + size * max(0.0, min(1.0, p)), detail)

        current = None
        try:
            current = "sync"
            self._set_stage(ep_id, "sync", "running")
            report = reporter("sync", "Sync")
            report(0.0, STAGE_DETAIL["sync"])
            chosen, mix, notes = buildlib.choose_sources([m for m in ep["media"] if not m.get("problem")])
            sources, mix_raw = buildlib.decode_sources(chosen, mix, work, check=ctx.check,
                                                       report=lambda p, label: report(0.9 * p, label))
            if len(chosen) > 1 and all(buildlib.is_mix(m) for m in chosen):
                notes += buildlib.join(sources)
            else:
                notes += buildlib.line_up(sources, mix_raw, check=ctx.check)
            with self._lock:
                ep = self._load(ep_id)
                used = {s.media_id: s for s in sources}
                for m in ep["media"]:
                    s = used.get(m["id"])
                    if s:
                        m["sync"] = {"offset": s.offset, "method": s.method, "confidence": s.confidence, "used": True}
                    else:
                        m["sync"] = {"used": False, "method": "Not used for the sound"}
                ep["build_notes"] = notes
                self._save(ep)
            self._set_stage(ep_id, "sync", "done")

            current = "voice"
            self._set_stage(ep_id, "voice", "running")
            report = reporter("voice", "Podcast voice")
            report(0.0, STAGE_DETAIL["voice"])
            _, _, total = buildlib.build_voice(ep_dir, sources, settings, check=ctx.check, report=report)
            for cache in ep_dir.glob("waveform*.json"):
                cache.unlink()
            with self._lock:
                ep = self._load(ep_id)
                ep["duration"] = total
                ep["voice"] = {"preset": "Podcast voice", "strength": settings.strength, "steps": settings.steps(),
                               "settings": self._voice_settings(ep, show), "made": now_iso()}
                self._save(ep)
            report(0.97, "Measuring how loud the talking is")
            self._talk_loudness(ep_id, check=ctx.check)
            self._set_stage(ep_id, "voice", "done")

            if transcribe:
                current = "transcribe"
                self._set_stage(ep_id, "transcribe", "running")
                report = reporter("transcribe", "Transcribe")
                report(0.0, STAGE_DETAIL["transcribe"])
                data = buildlib.transcribe(ep_dir / "voice.f32", work, sources, check=ctx.check, report=report)
                self._save_transcript(ep_id, data)
                self._set_stage(ep_id, "transcribe", "done")
            else:
                self._set_stage(ep_id, "transcribe", "later")
                with self._lock:
                    ep = self._load(ep_id)
                    note = "Speech to text isn't installed yet: double-click Update Cutzo, then Transcribe this episode."
                    ep["build_notes"] = [n for n in ep.get("build_notes", []) if n != note] + [note]
                    self._save(ep)

            current = "auto_edit"
            self._set_stage(ep_id, "auto_edit", "running")
            report = reporter("auto_edit", "Auto-edit")
            report(0.0, "Finding dead air")
            found = self._apply_real_auto_edit(ep_id)
            self._set_stage(ep_id, "auto_edit", "done")
            current = None
        finally:
            if current:
                self._set_stage(ep_id, current, "todo")
            buildlib.tidy(ep_dir)
        ep = self._load(ep_id)
        saved = sum(e["end"] - e["start"] for e in found)
        return {
            "summary": f"Built “{ep['title']}”: {fmt_duration(ep['duration'])} cleaned, "
                       f"{len(found)} long silences to shorten (saves {fmt_duration(saved)}). Ready to review.",
            "suggested_cuts": len(found),
            "notes": ep.get("build_notes", []),
        }

    def _save_transcript(self, ep_id: str, data: dict[str, Any]) -> None:
        """Store a real transcript (recording time) and let edits find it."""
        write_json(self.guard.writable(self._ep_dir(ep_id) / "transcript.json"), {
            "speakers": data.get("speakers") or [],
            "words": data.get("words") or [],
            "engine": data.get("engine"),
            "model": data.get("model"),
            "made": now_iso(),
        })
        with self._lock:
            ep = self._load(ep_id)
            ep["build_notes"] = [n for n in ep.get("build_notes", []) if not n.startswith("Speech to text isn't installed")]
            self._save(ep)

    def transcribe(self, episode: str, source: str = "you") -> dict[str, Any]:
        """Write down every word of a real episode that was built before speech to text was installed
        (or again, after a rebuild). Returns a job id."""
        ep_id = self.resolve_episode(episode)
        ep = self._load(ep_id)
        if ep.get("sample"):
            raise CutzoError("Sample episodes come with a sample transcript already.")
        if not (self._ep_dir(ep_id) / "voice.f32").exists() or not ep.get("duration"):
            raise CutzoError("Build the episode first; the transcript is made from its cleaned voices.")
        toolbox.require("ffmpeg")
        toolbox.require("parakeet-cli")
        toolbox.require_file("parakeet-model")
        with self._lock:
            self._refuse_if_busy(ep_id, "transcribe")
            job = self.jobs.submit(kind="transcribe", title=f"Transcribe {ep['title']}", episode=ep_id, source=source,
                                   steps=["Transcribe"], work=self._transcribe_work(ep_id))
        self._log(source, "transcribe", f"Started transcribing \u201c{ep['title']}\u201d", ep_id, details={"job": job["id"]})
        return {"job": job, "note": "Writes down every word with its time; about 2 minutes for a 90-minute episode."}

    def _transcribe_work(self, ep_id: str):
        def work(ctx: JobContext) -> dict[str, Any]:
            ep = self._load(ep_id)
            ep_dir = self._ep_dir(ep_id)
            work_dir = self.guard.writable(ep_dir / "work")
            done = False
            self._set_stage(ep_id, "transcribe", "running")
            try:
                sources: list[buildlib.Source] = []
                used = [m for m in ep["media"] if (m.get("sync") or {}).get("used") and m.get("person")]
                if len(used) > 1:  # several people on their own tracks: decode them to tell who speaks
                    ctx.step("Transcribe", 0.0, "Reading everyone's tracks")
                    chosen = [m for m in ep["media"] if (m.get("sync") or {}).get("used")]
                    sources, _ = buildlib.decode_sources(chosen, None, work_dir, check=ctx.check,
                                                         report=lambda p, label: ctx.step("Transcribe", 0.05 * p, label))
                    offsets = {m["id"]: float((m.get("sync") or {}).get("offset") or 0.0) for m in chosen}
                    for src in sources:
                        src.offset = offsets.get(src.media_id, 0.0)
                data = buildlib.transcribe(ep_dir / "voice.f32", work_dir, sources, check=ctx.check,
                                           report=lambda p, detail: ctx.step("Transcribe", 0.05 + 0.95 * p, detail))
                self._save_transcript(ep_id, data)
                done = True
            finally:
                had = bool(self._transcript(ep_id).get("words"))
                self._set_stage(ep_id, "transcribe", "done" if done or had else "later")
                buildlib.tidy(ep_dir)
            n = len(data["words"])
            return {"summary": f"Transcribed \u201c{ep['title']}\u201d: {n:,} words. Select words in Review to cut them.",
                    "words": n}

        return work

    def _apply_real_auto_edit(self, ep_id: str) -> list[dict[str, Any]]:
        """Suggest dead-air cuts from the cleaned voice track, keeping your own cuts and anything you brought back."""
        ep = self._load(ep_id)
        settings = {**buildlib.DEAD_AIR, **(ep.get("dead_air") or {})}
        cuts, levels = buildlib.find_dead_air(self._ep_dir(ep_id) / "voice.f32", longer_than=float(settings["longer_than"]),
                                              keep=float(settings["keep"]), threshold_db=settings.get("threshold_db"))
        dismissed = [tuple(r) for r in ep.get("dismissed", [])]
        cuts = [c for c in cuts if not any(c["start"] < b and a < c["end"] for a, b in dismissed)]
        with self._lock:
            ep = self._load(ep_id)
            ep["edits"] = [e for e in ep["edits"] if e.get("by") != "auto"]
            made = []
            for c in cuts:
                edit = self._new_edit(ep, by="auto", **c)
                ep["edits"].append(edit)
                made.append(edit)
            ep["dead_air"] = {"longer_than": settings["longer_than"], "keep": settings["keep"],
                              "threshold_db": settings.get("threshold_db"), "measured": levels}
            ep.pop("needs_rebuild", None)
            self._save(ep)
        return made

    def set_dead_air(self, episode: str, longer_than: Any = None, keep: Any = None, source: str = "you") -> dict[str, Any]:
        """How tight the dead-air trimming is: silences longer than `longer_than` seconds become `keep` seconds."""
        ep_id = self.resolve_episode(episode)
        ep = self._load(ep_id)
        if ep.get("sample"):
            raise CutzoError("Dead-air settings work on real episodes; sample episodes use pretend cuts.")
        self._require_built(ep)
        settings = {**buildlib.DEAD_AIR, **(ep.get("dead_air") or {})}
        try:
            new_long = float(longer_than) if longer_than not in (None, "") else float(settings["longer_than"])
            new_keep = float(keep) if keep not in (None, "") else float(settings["keep"])
        except (TypeError, ValueError):
            raise CutzoError("Give the times in seconds, like 1.0 and 0.5.") from None
        if not 0.3 <= new_long <= 10:
            raise CutzoError("“Longer than” must be between 0.3 and 10 seconds.")
        if not 0.1 <= new_keep < new_long:
            raise CutzoError("“Keep” must be at least 0.1 seconds and shorter than “longer than”.")
        with self._lock:
            self._refuse_if_busy(ep_id, "auto-edit")
            ep = self._load(ep_id)
            ep["dead_air"] = {**(ep.get("dead_air") or {}), "longer_than": new_long, "keep": new_keep}
            self._save(ep)
        made = self._apply_real_auto_edit(ep_id)
        ep = self._load(ep_id)
        saved = sum(e["end"] - e["start"] for e in made)
        summary = (f"Dead air: silences longer than {new_long:g} s now shrink to {new_keep:g} s "
                   f"({len(made)} places, saves {fmt_duration(saved)})")
        self._log(source, "set_dead_air", f"{summary} in {_q(ep['title'], 40)}", ep_id)
        return {"summary": summary, "suggested_cuts": len(made), "final_length": fmt_duration(self._final_duration(ep) or 0)}

    def set_voice(self, episode: str, strength: Any = None, source: str = "you") -> dict[str, Any]:
        """Change how strongly this episode's voices are cleaned (0-100), then rebuild to hear it."""
        ep_id = self.resolve_episode(episode)
        ep = self._load(ep_id)
        if ep.get("sample"):
            raise CutzoError("Cleanup settings work on real episodes; sample episodes are pretend.")
        try:
            value = int(round(float(strength)))
        except (TypeError, ValueError):
            raise CutzoError("Give the strength as a number from 0 (off) to 100 (strongest).") from None
        if not 0 <= value <= 100:
            raise CutzoError("The strength goes from 0 (off) to 100 (strongest).")
        with self._lock:
            self._refuse_if_busy(ep_id, "settings")
            ep = self._load(ep_id)
            ep["voice_settings"] = {**(ep.get("voice_settings") or {}), "strength": value}
            if ep["stages"].get("voice") == "done":
                ep["needs_rebuild"] = True
            self._save(ep)
        summary = f"Voice cleanup strength set to {value} of 100" + (" (rebuild to hear it)" if ep.get("needs_rebuild") else "")
        self._log(source, "set_voice", f"{summary} in {_q(ep['title'], 40)}", ep_id)
        return {"summary": summary, "needs_rebuild": bool(ep.get("needs_rebuild"))}

    # ========================================================== transcript
    def get_transcript(self, episode: str, include_cut: bool = False, fmt: str = "text",
                       source: str = "you", start: Any = None, end: Any = None) -> dict[str, Any]:
        ep = self._load(self.resolve_episode(episode))
        tr = self._transcript(ep["id"])
        a = parse_time(start, name="start") if start not in (None, "") else None
        b = parse_time(end, name="end") if end not in (None, "") else None
        if not tr.get("words"):
            if not ep.get("sample"):
                raise CutzoError("This episode has no transcript yet.",
                                 hint="Transcribe it first (transcribe_episode); it takes a few minutes.")
            raise CutzoError("This episode hasn't been transcribed yet.",
                             hint="Build the episode first; transcription is one of its steps.")
        self._log_read(source, "get_transcript", f"read the transcript of {_q(ep['title'], 40)}", ep["id"])
        cuts = self._cuts(ep)
        out: dict[str, Any] = {
            "episode": ep["id"],
            "title": ep["title"],
            "show": (showlib.get_show(ep["show"]) or {}).get("name"),
            "sample": bool(tr.get("sample")),
            "speakers": [s["name"] for s in tr.get("speakers", [])],
            "recording_length": fmt_duration(ep.get("duration") or 0),
            "final_length": fmt_duration(self._final_duration(ep) or 0),
            "note": "Times are in the original recording; Cutzo converts them for the finished file."
                    + (" This is sample text (Phase 1)." if tr.get("sample") else ""),
        }
        if fmt == "words":
            out["speakers_detail"] = tr.get("speakers", [])
            out["paragraphs"] = tx.paragraphs(tr, cuts)
        else:
            part = tr
            if a is not None or b is not None:  # just a stretch of a long episode
                lo, hi = a if a is not None else 0.0, b if b is not None else float("inf")
                part = {**tr, "words": [w for w in tr.get("words", []) if lo <= float(w["s"]) < hi]}
                out["stretch"] = f"{fmt_time(lo)} to {fmt_time(hi) if hi != float('inf') else 'the end'}"
            out["text"] = tx.as_text(part, cuts, include_cut=include_cut)
            out["words"] = len(part.get("words", []))
        return out

    # ============================================================== teaser
    def teaser_brief(self, episode: str, part: Any = 1, source: str = "you") -> dict[str, Any]:
        """What Claude needs to write the episode teaser: Nick's style and past descriptions, who's on,
        the chapters and the edited transcript (cuts and fillers left out), in parts of ~6,000 words."""
        ep = self._load(self.resolve_episode(episode))
        tr = self._transcript(ep["id"])
        if not tr.get("words"):
            raise CutzoError("This episode has no transcript yet, so there's nothing to write the teaser from.",
                             hint="Build the episode (or transcribe it) first; it takes a few minutes.")
        parts = teaserlib.split(teaserlib.lines(tr, self._cuts(ep)))
        try:
            n = int(part or 1)
        except (TypeError, ValueError):
            raise CutzoError("part must be a number, like 1 or 2.") from None
        if not 1 <= n <= len(parts):
            raise CutzoError(f"This transcript has {len(parts)} part{'s' if len(parts) != 1 else ''}; "
                             f"ask for 1 to {len(parts)}.")
        show = showlib.get_show(ep["show"]) or {}
        self._log_read(source, "get_teaser_brief",
                       f"read part {n} of {len(parts)} of {_q(ep['title'], 40)} to write its teaser", ep["id"])
        out: dict[str, Any] = {
            "episode": ep["id"],
            "title": ep["title"],
            "show": show.get("name"),
            "part": n,
            "parts": len(parts),
        }
        if n == 1:
            style = teaserlib.style_for(ep["show"])
            ordered = sorted(ep.get("chapters", []), key=lambda c: c["time"])
            out.update({
                "how": ("Read every part of the transcript first (call again with part=2, 3, ...), then write the "
                        "teaser and a suggested title in Nick's style and save them with save_teaser. Show Nick the "
                        "text; he can change it in Cutzo's Export tab."),
                "final_length": fmt_duration(self._final_duration(ep) or 0),
                "speakers": [s["name"] for s in tr.get("speakers", [])] or
                            ["(one combined recording: names aren't known; tell them apart from the talk, or ask Nick)"],
                "chapters": [{"title": c["title"], "at": fmt_time(c["time"])} for c in ordered],
                "where_it_goes": style["where"],
                "style": style["notes"],
                "title_format": style["title_format"],
                "past_teasers": style["examples"],
                "current_teaser": ep.get("teaser"),
                "sample": bool(ep.get("sample") or tr.get("sample")),
            })
        out["transcript"] = "\n".join(parts[n - 1])
        if n < len(parts):
            out["next"] = f"Call again with part={n + 1} for the rest of the transcript."
        return out

    def set_teaser(self, episode: str, text: Any = None, title: Any = None, source: str = "you") -> dict[str, Any]:
        """Store the episode teaser (and a suggested title) and write `<title> - Teaser.txt` beside the
        exports. Leave title out to keep it; text "" removes the teaser."""
        body = teaserlib.clean(text)
        if len(body) > teaserlib.MAX_TEASER_CHARS:
            raise CutzoError(f"That teaser is {len(body):,} characters; keep it under "
                             f"{teaserlib.MAX_TEASER_CHARS:,} (Nick's usually run 200 to 800).")
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            before = ep.get("teaser")
            if not body:
                if not before:
                    raise CutzoError("This episode has no teaser to remove.")
                ep.pop("teaser", None)
                summary = "Removed the episode teaser"
            else:
                if title is None:
                    new_title = (before or {}).get("title") or ""
                else:
                    new_title = " ".join(str(title).split())
                if len(new_title) > 200:
                    raise CutzoError("Keep the suggested title under 200 characters.")
                ep["teaser"] = {"text": body, "title": new_title, "by": source, "at": now_iso()}
                words = len(body.split())
                summary = f"{'Updated' if before else 'Wrote'} the episode teaser ({words} words)"
            self._write_teaser_file(ep)
            self._push_history(ep, {"op": "teaser", "before": before, "summary": summary})
            self._save(ep)
        self._log(source, "set_teaser", f"{summary} for {_q(ep['title'], 40)}", ep["id"])
        return {"teaser": ep.get("teaser"), "summary": summary}

    def _write_teaser_file(self, ep: dict[str, Any]) -> None:
        """Keep `<Show>/<Title> - Teaser.txt` (in the exports folder) in step with the stored teaser.
        Sample episodes have pretend exports, so no file for them either."""
        teaser = ep.get("teaser")
        if ep.get("sample"):
            if teaser:
                teaser.pop("file", None)
            return
        show = showlib.get_show(ep["show"]) or {}
        path = self.guard.writable(self.library / "exports" / safe_filename(show.get("name", "Other"))
                                   / f"{safe_filename(ep['title'])} - Teaser.txt")
        if not teaser:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        head = f"{teaser['title']}\n\n" if teaser.get("title") else ""
        path.write_text(head + teaser["text"] + "\n", encoding="utf-8")
        teaser["file"] = str(path)

    def waveform(self, episode: str, buckets: int = 1200, start: float | None = None,
                 end: float | None = None) -> dict[str, Any]:
        """Loudness outline for drawing. Real episodes can ask for a stretch (start..end, seconds) to zoom in."""
        ep_id = self.resolve_episode(episode)
        ep_dir = self._ep_dir(ep_id)
        voice = ep_dir / "voice.f32"
        if voice.exists():
            pcm = audio.Pcm(voice)
            a = max(0.0, float(start or 0.0))
            b = min(pcm.duration, float(end)) if end not in (None, "") else pcm.duration
            if b <= a:
                raise CutzoError("The end of the stretch must be after its start.")
            whole = a == 0.0 and b == pcm.duration
            cache = ep_dir / f"waveform-{int(buckets)}.json"
            mtime = voice.stat().st_mtime
            if whole:
                data = read_json(cache)
                if isinstance(data, dict) and data.get("mtime") == mtime:
                    return data
            data = {"buckets": int(buckets), "mtime": mtime, **audio.peaks(pcm, int(buckets), a, b)}
            if whole:
                write_json(self.guard.writable(cache), data)
            return data
        wav = ep_dir / "preview.wav"
        if not wav.exists():
            raise NotFound("No audio to draw yet. Build the episode first.")
        cache = ep_dir / "waveform.json"
        mtime = wav.stat().st_mtime
        data = read_json(cache)
        if isinstance(data, dict) and data.get("buckets") == buckets and data.get("mtime") == mtime:
            return data
        data = {"buckets": buckets, "mtime": mtime, **sample.waveform_peaks(wav, buckets)}
        write_json(self.guard.writable(cache), data)
        return data

    def preview_audio(self, episode: str, which: str = "cleaned") -> Path:
        """The file the player plays: the cleaned voices, or (real episodes) the original sound to compare."""
        ep_id = self.resolve_episode(episode)
        ep_dir = self._ep_dir(ep_id)
        if which == "original":
            path = ep_dir / "original.m4a"
            if not path.exists():
                raise NotFound("No original-sound copy for this episode.", hint="Build it again to make one.")
            return path
        for name in ("preview.m4a", "preview.wav"):
            if (ep_dir / name).exists():
                return ep_dir / name
        raise NotFound("No preview audio yet. Build the episode first.")

    # ============================================================== edits
    def cut_section(self, episode: str, start: Any, end: Any, source: str = "you", reason: str = "manual",
                    confirm: bool = False) -> dict[str, Any]:
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            self._require_built(ep)
            a = parse_time(start, name="start")
            b = parse_time(end, name="end")
            if b <= a:
                raise CutzoError(f"The end ({fmt_time(b, precise=True)}) must be after the start ({fmt_time(a, precise=True)}).")
            duration = float(ep["duration"])
            if a >= duration:
                raise CutzoError(f"{fmt_time(a)} is past the end of the recording ({fmt_time(duration)}).")
            b = min(b, duration)
            if b - a < 0.05:
                raise CutzoError("That's too short to cut (under a twentieth of a second).")
            if reason == "words" and not ep.get("sample"):
                a, b = self._word_cut_points(ep, a, b)
            self._check_big_cut(b - a, duration, confirm)
            words = self._words_between(ep["id"], a, b)
            edit = self._new_edit(ep, type="cut", start=a, end=b, reason=reason, text=words, by=source)
            ep["edits"].append(edit)
            summary = f"Cut {fmt_time(a, precise=True)}\u2013{fmt_time(b, precise=True)} ({b - a:.1f} s)" + (
                f": \u201c{_q(words)}\u201d" if words else "")
            self._push_history(ep, {"op": "add_edits", "ids": [edit["id"]], "summary": summary})
            self._save(ep)
        self._log(source, "cut_section", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        return {"edit": self._edit_view(edit, ep), "summary": summary, "final_length": fmt_duration(self._final_duration(ep))}

    def cut_words(self, episode: str, text: str, occurrence: Any = None, source: str = "you",
                  confirm: bool = False) -> dict[str, Any]:
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            self._require_built(ep)
            words = self._transcript(ep["id"]).get("words", [])
            if not words and not ep.get("sample"):
                raise CutzoError(NO_TRANSCRIPT)
            if not str(text or "").strip():
                raise CutzoError("Which words? Give the exact words to cut.")
            hits = tx.find_phrase(words, text)
            if not hits:
                raise NotFound(f"Couldn't find \u201c{_q(text)}\u201d in the transcript.",
                               hint="Use the exact words as they appear in the transcript.")
            cuts = self._cuts(ep)
            fresh = [h for h in hits if not all(tx.is_word_cut(words[i], cuts) for i in range(*h))]
            if not fresh:
                raise CutzoError(f"\u201c{_q(text)}\u201d is already cut.")
            places = ", ".join(fmt_time(words[i]["s"]) for i, _ in fresh)
            if occurrence in (None, "", 0) and len(fresh) > 1:
                raise CutzoError(
                    f"\u201c{_q(text)}\u201d is said {len(fresh)} times (at {places}). "
                    "Say which one with occurrence=1, 2, ... or occurrence='all'.",
                    data={"times": [fmt_time(words[i]["s"]) for i, _ in fresh]})
            if str(occurrence).lower() == "all":
                chosen = fresh
            elif occurrence in (None, "", 0):
                chosen = fresh[:1]
            else:
                try:
                    n = int(occurrence)
                except (TypeError, ValueError):
                    raise CutzoError("occurrence must be a number like 1 or 2, or 'all'.") from None
                if not 1 <= n <= len(fresh):
                    raise CutzoError(f"There are only {len(fresh)} places with those words (at {places}).")
                chosen = [fresh[n - 1]]
            total = sum(tx.span(words, i, j)[1] - tx.span(words, i, j)[0] for i, j in chosen)
            self._check_big_cut(total, float(ep["duration"]), confirm)
            made = []
            for i, j in chosen:
                said = " ".join(w["w"] for w in words[i:j])
                if ep.get("sample"):
                    a, b = tx.span(words, i, j)
                    a, b = round(max(0.0, a - 0.02), 3), round(min(float(ep["duration"]), b + 0.04), 3)
                else:
                    a, b = cutpoints.word_cut(self._ep_dir(ep["id"]) / "voice.f32", words, i, j, float(ep["duration"]))
                made.append(self._new_edit(ep, type="cut", start=a, end=b, reason="words", text=said, by=source))
            ep["edits"].extend(made)
            where = ", ".join(fmt_time(m["start"]) for m in made)
            summary = f"Cut the words \u201c{_q(text)}\u201d at {where}"
            self._push_history(ep, {"op": "add_edits", "ids": [m["id"] for m in made], "summary": summary})
            self._save(ep)
        self._log(source, "cut_words", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        return {"edits": [self._edit_view(m, ep) for m in made], "summary": summary,
                "final_length": fmt_duration(self._final_duration(ep))}

    def restore_cut(self, episode: str, edit_id: str, source: str = "you") -> dict[str, Any]:
        """Bring back a cut (or remove a camera choice) by its id."""
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            edit = next((e for e in ep["edits"] if e["id"] == edit_id), None)
            if not edit:
                raise NotFound(f"No edit called {edit_id!r} in this episode.",
                               hint="Edit ids are listed by get_episode (e1, e2, ...).")
            ep["edits"] = [e for e in ep["edits"] if e["id"] != edit_id]
            if edit["type"] == "cut" and edit.get("by") == "auto" and not ep.get("sample"):
                # Remember it, so finding dead air again doesn't suggest it a second time.
                ep.setdefault("dismissed", []).append([edit["start"], edit["end"]])
            if edit["type"] == "cut":
                summary = f"Brought back {fmt_time(edit['start'], precise=True)}\u2013{fmt_time(edit['end'], precise=True)}" + (
                    f" (\u201c{_q(edit['text'])}\u201d)" if edit.get("text") else "")
            else:
                summary = f"Removed the camera choice {edit.get('angle')} at {fmt_time(edit['start'])}"
            self._push_history(ep, {"op": "remove_edit", "edit": edit, "summary": summary})
            self._save(ep)
        self._log(source, "restore_cut", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        return {"restored": self._edit_view(edit, ep), "summary": summary,
                "final_length": fmt_duration(self._final_duration(ep))}

    def set_camera(self, episode: str, start: Any, end: Any, angle: str, source: str = "you") -> dict[str, Any]:
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            self._require_built(ep)
            cams = ep.get("cameras") or []
            if not cams:
                raise CutzoError("This episode has no camera video (it's audio only), so there are no angles to switch.")
            a = parse_time(start, name="start")
            b = min(parse_time(end, name="end"), float(ep["duration"]))
            if b <= a:
                raise CutzoError("The end must be after the start.")
            pick = self._match_angle(angle, cams)
            edit = self._new_edit(ep, type="camera", start=a, end=b, angle=pick, by=source, reason="chosen")
            ep["edits"].append(edit)
            summary = f"Camera on {pick} from {fmt_time(a)} to {fmt_time(b)}"
            self._push_history(ep, {"op": "add_edits", "ids": [edit["id"]], "summary": summary})
            self._save(ep)
        self._log(source, "set_camera", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        return {"edit": self._edit_view(edit, ep), "summary": summary}

    def add_chapter(self, episode: str, time: Any, title: str, source: str = "you",
                    segment: bool | None = None) -> dict[str, Any]:
        """Add (or rename) a chapter. `segment`: for shows with music breaks between segments (The New
        Heretics), whether this chapter starts a new segment, with a break before it."""
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            self._require_built(ep)
            t = parse_time(time, name="time")
            if t > float(ep["duration"]):
                raise CutzoError(f"{fmt_time(t)} is past the end of the recording ({fmt_time(ep['duration'])}).")
            title = " ".join(str(title or "").split())
            if not title:
                raise CutzoError("Give the chapter a title.")
            if len(title) > 100:
                raise CutzoError("Chapter titles must be 100 characters or less (YouTube's limit).")
            near = next((c for c in ep["chapters"] if abs(c["time"] - t) <= 1.0), None)
            if near:
                before = dict(near)
                near.update(title=title, by=source)
                if segment is not None:
                    near["segment"] = bool(segment)
                summary = f"Renamed the chapter at {fmt_time(near['time'])} to \u201c{title}\u201d"
                self._push_history(ep, {"op": "update_chapter", "before": before, "summary": summary})
                chapter = near
            else:
                chapter = self._new_chapter(ep, t, title, by=source)
                if segment:
                    chapter["segment"] = True
                ep["chapters"].append(chapter)
                ep["chapters"].sort(key=lambda c: c["time"])
                kind = "segment" if segment else "chapter"
                summary = f"Added {kind} \u201c{title}\u201d at {fmt_time(t)}"
                self._push_history(ep, {"op": "add_chapter", "id": chapter["id"], "summary": summary})
            self._save(ep)
        self._log(source, "add_chapter", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        show = showlib.get_show(ep["show"]) or {}
        first = min(ep["chapters"], key=lambda c: c["time"])["id"] == chapter["id"]
        voiced = None if first else showlib.segment_title_for(show, title)  # the first chapter starts the talk
        layout = self._layout(ep, show)
        at = None
        if layout:
            ordered = sorted(ep["chapters"], key=lambda c: c["time"])
            at = layout.chapters[[c["id"] for c in ordered].index(chapter["id"])]["time"]
        return {"chapter": self._chapter_view(chapter, ep, show, at), "summary": summary,
                "voiced_title": voiced["name"] if voiced else None}

    def set_segment(self, episode: str, chapter_id: str, segment: Any = True, source: str = "you") -> dict[str, Any]:
        """Mark a chapter as the start of a new segment (a music break plays before it) or not."""
        on = segment if isinstance(segment, bool) else str(segment).strip().lower() in ("1", "true", "yes", "on")
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            ordered = sorted(ep["chapters"], key=lambda c: c["time"])
            chapter = next((c for c in ordered if c["id"] == chapter_id), None)
            if not chapter:
                raise NotFound(f"No chapter called {chapter_id!r} in this episode.")
            if on and chapter is ordered[0]:
                raise CutzoError("The first chapter starts the episode, so it can't have a break before it.")
            before = dict(chapter)
            chapter["segment"] = on
            what = "starts a new segment (music break before it)" if on else "is a new topic (no music break)"
            summary = f"\u201c{chapter['title']}\u201d {what}"
            self._push_history(ep, {"op": "update_chapter", "before": before, "summary": summary})
            self._save(ep)
        self._log(source, "set_segment", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        show = showlib.get_show(ep["show"]) or {}
        return {"chapter": self._chapter_view(chapter, ep, show), "summary": summary}

    def set_video_title(self, episode: str, label: Any = None, title: Any = None, guests: Any = None,
                        source: str = "you") -> dict[str, Any]:
        """The episode title graphic's three lines (The New Heretics): a label ("HALLOWEEN SPECIAL:"),
        the title, and a guests line ("ft. Two exJWs"). Leave a line out to keep it; "" clears it."""
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            lines = titles.lines_for(ep)
            for key, value in (("label", label), ("title", title), ("guests", guests)):
                if value is not None:
                    text = " ".join(str(value).split())
                    if len(text) > 80:
                        raise CutzoError("Keep each line of the title graphic under 80 characters.")
                    lines[key] = text
            if not any(lines.values()):
                raise CutzoError("The title graphic needs at least one line.")
            before = ep.get("video_title")
            ep["video_title"] = lines
            shown = " / ".join(v for v in (lines["label"].upper(), lines["title"].upper(), lines["guests"]) if v)
            summary = f"Title graphic set to \u201c{shown}\u201d"
            self._push_history(ep, {"op": "video_title", "before": before, "summary": summary})
            self._save(ep)
        self._log(source, "set_video_title", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        return {"video_title": lines, "summary": summary}

    def remove_chapter(self, episode: str, chapter_id: str, source: str = "you") -> dict[str, Any]:
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            chapter = next((c for c in ep["chapters"] if c["id"] == chapter_id), None)
            if not chapter:
                raise NotFound(f"No chapter called {chapter_id!r} in this episode.")
            ep["chapters"] = [c for c in ep["chapters"] if c["id"] != chapter_id]
            summary = f"Removed chapter \u201c{chapter['title']}\u201d at {fmt_time(chapter['time'])}"
            self._push_history(ep, {"op": "remove_chapter", "chapter": chapter, "summary": summary})
            self._save(ep)
        self._log(source, "remove_chapter", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        return {"summary": summary}

    # ------------------------------------------------------- highlight picks
    PICK_KINDS = {"cold_open": "cold open", "outro": "sign-off"}
    PICK_LIMITS = {"cold_open": 45.0, "outro": 90.0}

    def _pick_word(self, ep: dict[str, Any], key: str) -> str:
        """What the show calls a pick: The New Heretics opens with a teaser, The Dig with a cold open."""
        return "teaser" if key == "cold_open" and ep.get("show") == "the-new-heretics" else self.PICK_KINDS[key]

    def _pick_kind(self, kind: str) -> str:
        k = str(kind or "").strip().lower().replace("-", " ").replace("_", " ")
        if k in ("cold open", "coldopen", "teaser", "highlight", "highlights", "intro", "opening", "open"):
            return "cold_open"
        if k in ("outro", "sign off", "signoff", "ending", "end", "closing", "outro clip", "outro clips"):
            return "outro"
        raise CutzoError(f"“{kind}” isn't a kind of pick. Use “cold open” (highlights at the start) "
                         "or “sign-off” (clips at the end).")

    def add_pick(self, episode: str, kind: str, start: Any, end: Any, source: str = "you") -> dict[str, Any]:
        """Add a stretch of the recording to the cold open (highlights at the start) or the sign-off (at the end)."""
        key = self._pick_kind(kind)
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            self._require_built(ep)
            a = parse_time(start, name="start")
            b = min(parse_time(end, name="end"), float(ep["duration"]))
            if b <= a:
                raise CutzoError("The end must be after the start.")
            if b - a < 0.5:
                raise CutzoError("That's too short to use (under half a second).")
            picks = ep.setdefault("picks", {}).setdefault(key, [])
            total = sum(p["end"] - p["start"] for p in picks) + (b - a)
            limit = self.PICK_LIMITS[key]
            if total > limit:
                raise CutzoError(f"The {self._pick_word(ep, key)} would run {fmt_duration(total)}; keep it under "
                                 f"{fmt_duration(limit)}.", hint="Pick a shorter stretch, or remove one first.")
            pick = {"id": self._next_id(ep, "p"), "start": round(a, 3), "end": round(b, 3), "by": source, "at": now_iso()}
            picks.append(pick)
            summary = (f"Added {fmt_time(a, precise=True)}–{fmt_time(b, precise=True)} ({b - a:.1f} s) "
                       f"to the {self._pick_word(ep, key)}")
            self._push_history(ep, {"op": "add_pick", "kind": key, "id": pick["id"], "summary": summary})
            self._save(ep)
        self._log(source, "add_pick", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        out = {"pick": self._pick_view(pick, key), "summary": summary, "total": fmt_duration(total)}
        room = self._cold_open_room(ep)
        if key == "cold_open" and room and total > room + 0.05:
            out["note"] = (f"The cold open is now {total:.1f} s; Nick keeps it under {room:.1f} s so it ends before the "
                           "full band comes in. Longer works (the music waits for it), but shorter is his style.")
        return out

    def _cold_open_room(self, ep: dict[str, Any]) -> float | None:
        """How long a cold open fits before the show's music really starts (The Dig: ~18.6 s), or None."""
        recipe = recipes.RECIPES.get((showlib.get_show(ep["show"]) or {}).get("recipe") or "") or {}
        return (recipe.get("intro") or {}).get("cold_open_room")

    def remove_pick(self, episode: str, pick_id: str, source: str = "you") -> dict[str, Any]:
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            for key, picks in (ep.get("picks") or {}).items():
                pick = next((p for p in picks if p["id"] == pick_id), None)
                if pick:
                    picks.remove(pick)
                    break
            else:
                raise NotFound(f"No cold-open or sign-off pick called {pick_id!r} in this episode.")
            summary = (f"Removed {fmt_time(pick['start'], precise=True)}–{fmt_time(pick['end'], precise=True)} "
                       f"from the {self._pick_word(ep, key)}")
            self._push_history(ep, {"op": "remove_pick", "kind": key, "pick": pick, "summary": summary})
            self._save(ep)
        self._log(source, "remove_pick", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        return {"summary": summary}

    def _pick_view(self, pick: dict[str, Any], key: str) -> dict[str, Any]:
        return {**pick, "kind": key, "kind_label": self.PICK_KINDS[key], "start_text": fmt_time(pick["start"], precise=True),
                "end_text": fmt_time(pick["end"], precise=True), "length": round(pick["end"] - pick["start"], 3)}

    # ----------------------------------------------------------- extras
    # One-off pieces for one episode (the haiku in Spooky Sode): a sound or video file, or a show-kit
    # item, played at a time in the recording. Stored as ep["inserts"] (ep["extras"] holds the export's
    # captions and chapter-list files); the recipes place them (recipes.extras_by_time).
    EXTRA_KIND_WORDS = {"voice": "spoken", "music": "music", "video": "video"}
    EXTRA_LIMIT = 600.0  # seconds, per extra

    def _extra_kind(self, kind: Any) -> str | None:
        k = str(kind or "").strip().lower()
        if not k or k == "auto":
            return None
        if k in ("voice", "spoken", "speech", "talk", "talking", "voiceover", "voice-over", "voice over", "sound", "audio"):
            return "voice"
        if k in ("music", "song", "cue", "jingle"):
            return "music"
        if k in ("video", "clip", "picture"):
            return "video"
        raise CutzoError(f"“{kind}” isn't a kind of extra. Use spoken, music or video.")

    def _extra_source(self, what: str, show: dict[str, Any]) -> dict[str, Any]:
        """A file on this PC, or a show-kit item (by id or name): what it is and how long it lasts."""
        raw = str(what or "").strip().strip('"').strip()
        if not raw:
            raise CutzoError("Say which file or show-kit item the extra is.")
        path = Path(raw)
        if path.is_file():
            if kind_of(path) not in ("audio", "video"):
                raise CutzoError(f"{path.name} isn't a sound or video file.")
            info = ffmpeg.probe(path)
            return {"file": str(path), "name": path.stem, "seconds": info.get("duration"),
                    "has_sound": bool(info.get("audio")), "has_picture": bool(info.get("video")), "music": False}
        want = raw.lower()
        choices = [a for a in kit.list_assets(show.get("id")) if a["kind"] in ("audio", "video")]
        exact = [a for a in choices if want in (a["id"], a["name"].lower(), a["name"].lower().rsplit(".", 1)[0])]
        loose = exact or [a for a in choices if want in a["name"].lower() or want in a["id"]]
        if len(loose) != 1:
            if len(loose) > 1:
                names = ", ".join(a["name"] for a in loose[:8])
                raise CutzoError(f"“{raw}” matches several show-kit items: {names}. Say which one.")
            raise NotFound(f"Can't find a file or show-kit item called “{raw}”.",
                           hint="Paste the file's full path (in File Explorer: right-click it, Copy as path), "
                                "or name a show-kit item such as Haiku Intro.")
        asset = loose[0]
        seconds, has_picture = asset.get("seconds"), asset["kind"] == "video"
        has_sound = asset["kind"] == "audio"
        src = kit.source_path(asset, self.drive)
        if (seconds is None or has_picture) and src is not None and src.exists():
            info = ffmpeg.probe(src)
            seconds = seconds or info.get("duration")
            has_sound = bool(info.get("audio"))
        if not seconds:
            raise CutzoError(f"Can't tell how long {asset['name']} is.",
                             hint="Check that Google Drive is running and the file is in the show kit's folder.")
        music = "music" in asset["use"].lower() or "cue" in asset["use"].lower()
        return {"asset": asset["id"], "name": asset["name"].rsplit(".", 1)[0], "seconds": seconds,
                "has_sound": has_sound, "has_picture": has_picture, "music": music}

    def _extra_where(self, ep: dict[str, Any], time: Any, chapter: Any) -> tuple[float, str | None]:
        """(recording time, chapter id or None): at a chapter (by id or title), or at a time, which counts
        as the chapter there if one is within half a second."""
        chapters = sorted(ep.get("chapters", []), key=lambda c: c["time"])
        if chapter not in (None, ""):
            want = " ".join(str(chapter).split()).lower()
            found = [c for c in chapters if want in (c["id"].lower(), c["title"].lower())] or \
                    [c for c in chapters if want in c["title"].lower()]
            if len(found) != 1:
                names = ", ".join(f"“{c['title']}”" for c in chapters) or "none yet"
                raise NotFound(f"No single chapter matches “{chapter}”. Chapters: {names}.")
            return float(found[0]["time"]), found[0]["id"]
        if time in (None, ""):
            raise CutzoError("Say where the extra goes: a time in the recording (like 41:20) or a chapter.")
        t = parse_time(time, name="time")
        kept = timeline.kept(float(ep["duration"]), self._cuts(ep))
        if not kept or t >= kept[-1][1] - 0.5:
            raise CutzoError(f"{fmt_time(t)} is at or after the end of the talk, so there's nothing to pause there.",
                             hint="Pick an earlier time, or a chapter.")
        near = next((c for c in chapters if abs(float(c["time"]) - t) <= 0.5), None)
        return (float(near["time"]), near["id"]) if near else (round(t, 3), None)

    def add_insert(self, episode: str, file: str, time: Any = None, chapter: Any = None, kind: Any = None,
                   start: Any = None, end: Any = None, gain_db: Any = None, label: Any = None,
                   source: str = "you") -> dict[str, Any]:
        """Add an extra: a sound or video file (or a show-kit item) played at a time in the recording or at a
        chapter. The talk pauses there; at a chapter it plays in the pause or music break before it."""
        ep_id = self.resolve_episode(episode)
        ep = self._load(ep_id)
        self._require_built(ep)
        if ep.get("sample"):
            raise CutzoError("Extras work on real episodes; sample episodes are pretend.")
        show = showlib.get_show(ep["show"]) or {}
        what = self._extra_source(file, show)  # reads the file's details outside the lock
        with self._lock:
            ep = self._load(ep_id)
            t, chapter_id = self._extra_where(ep, time, chapter)
            whole = float(what["seconds"])
            a = 0.0 if start in (None, "") else parse_time(start, name="start")
            b = whole if end in (None, "") else min(parse_time(end, name="end"), whole)
            if a >= whole:
                raise CutzoError(f"It starts after the end of {what['name']} ({whole:.1f} s long).")
            if b - a < 0.1:
                raise CutzoError("The part to use must be longer than a tenth of a second.")
            if b - a > self.EXTRA_LIMIT:
                raise CutzoError(f"Keep an extra under {fmt_duration(self.EXTRA_LIMIT)}.",
                                 hint="Use part of the file (a start and an end).")
            k = self._extra_kind(kind) or ("video" if what["has_picture"] and show.get("recipe") == "the-new-heretics"
                                           else "music" if what["music"] else "voice")
            if k == "video" and not what["has_picture"]:
                raise CutzoError(f"{what['name']} has no picture, so it can't be a video extra.",
                                 hint="Make it spoken or music.")
            if not what["has_sound"] and k != "video":
                raise CutzoError(f"{what['name']} has no sound.")
            try:
                gain = 0.0 if gain_db in (None, "") else float(gain_db)
            except (TypeError, ValueError):
                raise CutzoError("The level change must be a number of decibels, like -3 or 2.") from None
            if abs(gain) > 20:
                raise CutzoError("Keep the level change between -20 and +20 dB.")
            name = " ".join(str(label or "").split())[:80] or what["name"]
            item = {"id": self._next_id(ep, "i"), "time": t, "chapter": chapter_id, "kind": k,
                    "start": round(a, 3), "end": round(b, 3), "seconds": round(whole, 3), "gain_db": round(gain, 1),
                    "label": name, "has_sound": what["has_sound"], "by": source, "at": now_iso()}
            item.update({key: what[key] for key in ("file", "asset") if what.get(key)})
            ep.setdefault("inserts", []).append(item)
            view = self._insert_view(item, ep, show)
            summary = f"Added {name} ({view['kind_label']}, {b - a:.1f} s) {view['where'][0].lower()}{view['where'][1:]}"
            self._push_history(ep, {"op": "add_insert", "id": item["id"], "summary": summary})
            self._save(ep)
        self._log(source, "add_insert", f"{summary} in {_q(ep['title'], 40)}", ep_id)
        layout = self._layout(ep, show)
        return {"extra": view, "summary": summary,
                "finished_length": fmt_duration(layout.total) if layout else None}

    def remove_insert(self, episode: str, insert_id: str, source: str = "you") -> dict[str, Any]:
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            item = next((x for x in ep.get("inserts") or [] if x["id"] == insert_id), None)
            if not item:
                raise NotFound(f"No extra called {insert_id!r} in this episode.")
            ep["inserts"] = [x for x in ep["inserts"] if x["id"] != insert_id]
            summary = f"Removed the extra {item['label']}"
            self._push_history(ep, {"op": "remove_insert", "insert": item, "summary": summary})
            self._save(ep)
            for path in self._insert_files(ep["id"], insert_id).values():  # its working copies (made again on undo)
                self.guard.writable(path).unlink(missing_ok=True)
        self._log(source, "remove_insert", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        return {"summary": summary}

    def _insert_view(self, item: dict[str, Any], ep: dict[str, Any], show: dict[str, Any]) -> dict[str, Any]:
        chapters = sorted(ep.get("chapters", []), key=lambda c: c["time"])
        chapter = next((c for c in chapters if c["id"] == item.get("chapter")), None)
        t = float(chapter["time"]) if chapter else float(item["time"])
        if chapter and chapter is chapters[0]:
            where = "At the start of the talk"
        elif chapter and (show.get("breaks_at") != "segments" or chapter.get("segment")):
            where = f"In the break before “{chapter['title']}”"
        elif chapter:
            where = f"Before the new topic “{chapter['title']}”"
        else:
            where = f"At {fmt_time(t)} in the talk"
        source = item.get("file") or (kit.get_asset(item.get("asset") or "") or {}).get("name") or item.get("asset")
        length = float(item["end"]) - float(item["start"])
        part = None
        if float(item["start"]) > 0.05 or float(item["end"]) < float(item["seconds"]) - 0.05:
            part = f"{fmt_time(item['start'], precise=True)}–{fmt_time(item['end'], precise=True)} of it"
        return {**item, "where": where, "at_time": t, "time_text": fmt_time(t), "source": source,
                "kind_label": self.EXTRA_KIND_WORDS.get(item.get("kind"), "spoken"), "length": round(length, 3),
                "length_text": f"{length:.1f} s", "part": part}

    def _insert_files(self, ep_id: str, insert_id: str) -> dict[str, Path]:
        folder = self._ep_dir(ep_id) / "inserts"
        return {"sound": folder / f"{insert_id}.f32", "sound_info": folder / f"{insert_id}.json",
                "picture": folder / f"{insert_id}-1080p.mp4", "picture_info": folder / f"{insert_id}-1080p.json"}

    def _insert_copy(self, ep_id: str, insert_id: str, which: str, make) -> Path:
        """A working copy of an extra's file (just the part used), made the first time an export needs it and
        again if the file changes; if the file has gone, the copy from last time still works."""
        item = next((x for x in self._load(ep_id).get("inserts") or [] if x["id"] == insert_id), None)
        if not item or not item.get("file"):
            raise NotFound(f"No extra called {insert_id!r} in this episode.")
        files = self._insert_files(ep_id, insert_id)
        copy, info_path = self.guard.writable(files[which]), self.guard.writable(files[which + "_info"])
        src = Path(item["file"])
        want: dict[str, Any] = {"source": str(src), "start": item["start"], "end": item["end"]}
        try:
            stat = src.stat()
        except OSError:
            if copy.exists():
                return copy
            raise NotFound(f"Can't find the file for the extra {item['label']} ({src}).",
                           hint="Put the file back, or remove the extra.") from None
        want.update(size=stat.st_size, mtime=int(stat.st_mtime))
        info = read_json(info_path)
        if copy.exists() and isinstance(info, dict) and all(info.get(k) == v for k, v in want.items()):
            return copy
        copy.parent.mkdir(parents=True, exist_ok=True)
        tmp = copy.with_name(copy.stem + ".part" + copy.suffix)
        try:
            make(src, tmp, float(item["start"]), float(item["end"]) - float(item["start"]))
            tmp.replace(copy)
        finally:
            tmp.unlink(missing_ok=True)
        write_json(info_path, want)
        return copy

    def _insert_sound(self, ep_id: str, insert_id: str, check=None) -> Path:
        """An extra's sound (the part used) as a stereo working file in the episode folder."""
        return self._insert_copy(ep_id, insert_id, "sound", lambda src, dst, start, length: ffmpeg.decode(
            src, dst, channels=2, start=start, length=length, check=check))

    def _insert_loudness(self, ep_id: str, insert_id: str, check=None) -> float | None:
        """How loud an extra is (LUFS, measured in stereo once per copy); None if too short or silent to tell."""
        copy = self._insert_sound(ep_id, insert_id, check=check)
        info_path = self.guard.writable(self._insert_files(ep_id, insert_id)["sound_info"])
        info = read_json(info_path)
        info = info if isinstance(info, dict) else {}
        if "lufs" not in info:
            lufs = ffmpeg.loudness(copy, channels=2, check=check, peak=False)["integrated"]
            info["lufs"] = lufs if lufs > -69.0 else None
            write_json(info_path, info)
        return info["lufs"]

    def _insert_picture(self, ep_id: str, insert_id: str, check=None, progress=None) -> Path:
        """A video extra's picture (the part used) at 1080p, 29.97 fps, letterboxed if it isn't 16:9."""
        return self._insert_copy(ep_id, insert_id, "picture", lambda src, dst, start, length: kit.fit_1080p(
            src, dst, start=start, length=length, check=check, progress=progress))

    def swap_asset(self, episode: str, slot: str, asset: str, source: str = "you") -> dict[str, Any]:
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            show = showlib.get_show(ep["show"]) or {}
            sdef = self._match_slot(show, slot)
            choice = self._match_choice(sdef, asset)
            overrides = ep.setdefault("assets", {})
            before = overrides.get(sdef["slot"])
            if choice == sdef["default"]:
                overrides.pop(sdef["slot"], None)
            else:
                overrides[sdef["slot"]] = choice
            name = (kit.get_asset(choice) or {}).get("name", choice)
            summary = f"{sdef['label']} set to {name}"
            self._push_history(ep, {"op": "asset", "slot": sdef["slot"], "before": before, "summary": summary})
            self._save(ep)
        self._log(source, "swap_asset", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        return {"slot": self._slot_view(show, sdef, ep.get("assets")), "summary": summary}

    def undo(self, episode: str, source: str = "you") -> dict[str, Any]:
        with self._lock:
            ep = self._load(self.resolve_episode(episode))
            history = ep.setdefault("history", [])
            while history:
                item = history.pop()
                if self._apply_undo(ep, item):
                    self._save(ep)
                    summary = f"Undid: {item['summary']}"
                    break
            else:
                self._save(ep)
                raise CutzoError("Nothing to undo in this episode.")
        self._log(source, "undo", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        return {"summary": summary, "final_length": fmt_duration(self._final_duration(ep) or 0)}

    def _apply_undo(self, ep: dict[str, Any], item: dict[str, Any]) -> bool:
        op = item.get("op")
        if op == "add_edits":
            ids = set(item.get("ids", []))
            before = len(ep["edits"])
            ep["edits"] = [e for e in ep["edits"] if e["id"] not in ids]
            return len(ep["edits"]) < before
        if op == "remove_edit":
            ep["edits"].append(item["edit"])
            span = [item["edit"]["start"], item["edit"]["end"]]
            if span in ep.get("dismissed", []):
                ep["dismissed"].remove(span)
            return True
        if op == "add_chapter":
            before = len(ep["chapters"])
            ep["chapters"] = [c for c in ep["chapters"] if c["id"] != item["id"]]
            return len(ep["chapters"]) < before
        if op == "update_chapter":
            for c in ep["chapters"]:
                if c["id"] == item["before"]["id"]:
                    c.clear()
                    c.update(item["before"])
                    return True
            return False
        if op == "remove_chapter":
            ep["chapters"].append(item["chapter"])
            ep["chapters"].sort(key=lambda c: c["time"])
            return True
        if op == "add_pick":
            picks = (ep.get("picks") or {}).get(item["kind"], [])
            before = len(picks)
            picks[:] = [p for p in picks if p["id"] != item["id"]]
            return len(picks) < before
        if op == "remove_pick":
            ep.setdefault("picks", {}).setdefault(item["kind"], []).append(item["pick"])
            ep["picks"][item["kind"]].sort(key=lambda p: int(re.sub(r"\D", "", p["id"]) or 0))
            return True
        if op == "add_insert":
            before = len(ep.get("inserts") or [])
            ep["inserts"] = [x for x in ep.get("inserts") or [] if x["id"] != item["id"]]
            return len(ep["inserts"]) < before
        if op == "remove_insert":
            ep.setdefault("inserts", []).append(item["insert"])
            ep["inserts"].sort(key=lambda x: int(re.sub(r"\D", "", x["id"]) or 0))
            return True
        if op == "media_order":
            for m in ep.get("media", []):
                if m["id"] in item["before"]:
                    if item["before"][m["id"]] is None:
                        m.pop("part", None)
                    else:
                        m["part"] = item["before"][m["id"]]
            return True
        if op == "teaser":
            if item.get("before") is None:
                ep.pop("teaser", None)
            else:
                ep["teaser"] = item["before"]
            self._write_teaser_file(ep)
            return True
        if op == "video_title":
            if item.get("before") is None:
                ep.pop("video_title", None)
            else:
                ep["video_title"] = item["before"]
            return True
        if op == "asset":
            overrides = ep.setdefault("assets", {})
            if item.get("before") is None:
                overrides.pop(item["slot"], None)
            else:
                overrides[item["slot"]] = item["before"]
            return True
        return False

    # ============================================================== export
    def export(self, episode: str, targets: Iterable[str] | str | None = None, confirm: bool = False,
               source: str = "you") -> dict[str, Any]:
        ep_id = self.resolve_episode(episode)
        ep = self._load(ep_id)
        self._require_built(ep)
        show = showlib.get_show(ep["show"]) or {}
        if isinstance(targets, str):
            targets = [t for t in re.split(r"[,+;]", targets) if t.strip()]
        targets = list(targets or show.get("outputs", []))
        chosen: list[dict[str, Any]] = []
        for t in targets:
            p = presets.find_preset(t)
            if not p:
                names = ", ".join(x["id"] for x in presets.list_presets(include_reserve=True))
                raise CutzoError(f"Unknown export target \u201c{t}\u201d. Choose from: {names}.")
            if p["id"] not in [c["id"] for c in chosen]:
                chosen.append(p)
        if not ep.get("sample"):
            video = [p for p in chosen if p["kind"] == "video"]
            has_picture = (recipes.RECIPES.get(show.get("recipe") or "") or {}) is recipes.NEW_HERETICS
            if video and not has_picture:
                raise CutzoError(f"{show.get('name', 'This show')} is audio only, so there's no video to export.",
                                 hint="Export podcast-mp3 (or podcast-aac) for this episode.")
            unmade = [p["label"] for p in video if not p.get("spec")]
            if unmade:
                raise CutzoError(f"{', '.join(unmade)}: Cutzo makes video at 1080p for now (your pick).",
                                 hint="Export youtube-1080p (or substack-video) instead.")
            if not (self._ep_dir(ep_id) / "voice.f32").exists():
                raise CutzoError("This episode's cleaned audio is missing, so it can't be exported.",
                                 hint="Build the episode again first.")
            toolbox.require("ffmpeg")
        plan = [{"target": p["id"], "label": p["label"], "kind": p["kind"], "file": self._export_path(ep, show, p)}
                for p in chosen]
        mine = {x["file"].lower() for x in ep.get("exports", [])}
        existing = [x for x in ep.get("exports", []) if x["target"] in {c["id"] for c in chosen} and x["status"] == "done"]
        on_disk = [item for item in plan if Path(item["file"]).exists() and item["file"].lower() not in mine]
        if (existing or on_disk) and not confirm:
            labels = ", ".join([x["label"] for x in existing] + [x["label"] for x in on_disk])
            raise NeedsConfirmation(
                f"{labels} {'is' if len(existing) + len(on_disk) == 1 else 'are'} already exported for this episode.",
                hint="Repeat with confirm=true to replace the finished file(s); check with Nick first.",
                data={"existing": [x["file"] for x in existing] + [x["file"] for x in on_disk]})
        work = self._export_work(ep_id, plan) if ep.get("sample") else self._real_export_work(ep_id, plan)
        with self._lock:
            self._refuse_if_busy(ep_id, "export")
            job = self.jobs.submit(kind="export", title=f"Export {ep['title']}", episode=ep_id, source=source,
                                   steps=[f"Render {p['label']}" for p in chosen], work=work)
        labels = ", ".join(p["label"] for p in chosen)
        self._log(source, "export", f"Started exporting {labels} for {_q(ep['title'], 40)}", ep_id,
                  details={"job": job["id"]})
        note = SAMPLE_EXPORT_NOTE if ep.get("sample") else f"Writes the finished file(s) to {self.library / 'exports'}."
        return {"job": job, "targets": plan, "note": note}

    def _real_export_work(self, ep_id: str, plan: list[dict[str, Any]]):
        def work(ctx: JobContext) -> dict[str, Any]:
            ep = self._load(ep_id)
            show = showlib.get_show(ep["show"]) or {}
            ep_dir = self._ep_dir(ep_id)
            voice_path = ep_dir / "voice.f32"
            ctx.step("Laying out the episode", 0.01, "Intro, breaks and ending from the show kit")
            talk = self._talk_loudness(ep_id, check=ctx.check)
            kit_dir = self.guard.writable(self.library / "kit")

            def kit_file(asset_id: str) -> Path:
                if asset_id.startswith("insert:"):  # an extra: a file of Nick's
                    return self._insert_sound(ep_id, asset_id[7:], check=ctx.check)
                return kit.local_copy(asset_id, kit_dir=kit_dir, drive=self.drive, check=ctx.check)

            def length_of(asset_id: str) -> float:
                asset = kit.get_asset(asset_id) or {}
                if asset.get("kind") == "video" and asset.get("seconds"):
                    return float(asset["seconds"])  # its picture: don't decode a long video's sound for this
                return audio.Pcm(kit_file(asset_id), channels=2).duration

            def loudness_of(asset_id: str) -> float | None:
                if asset_id.startswith("insert:"):
                    return self._insert_loudness(ep_id, asset_id[7:], check=ctx.check)
                return kit.loudness(asset_id, kit_dir=kit_dir, drive=self.drive, check=ctx.check)

            layout = recipes.plan_episode(ep, show, voice_path=voice_path, talk_lufs=talk, kit_file=kit_file,
                                          length_of=length_of, loudness_of=loudness_of)
            mix_path = self.guard.writable(ep_dir / "mix.f32")
            work_dir = self.guard.writable(ep_dir / "work-export")
            entries = []
            tags = {"title": ep["title"], "artist": show.get("network") or show.get("name", ""),
                    "album": show.get("name", ""), "genre": "Podcast", "date": ep["created"][:4],
                    "encoded_by": f"Cutzo {__version__}"}
            try:
                render.mix(layout, mix_path, check=ctx.check, progress=lambda p: ctx.step("Mixing", 0.05 + 0.15 * p))
                n = len(plan)
                for k, item in enumerate(plan):
                    preset = presets.get_preset(item["target"]) or {}
                    spec = preset["spec"]
                    dst = self.guard.writable(item["file"])
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    base, size = 0.2 + 0.8 * k / n, 0.8 / n

                    def step(label: str, share: float, detail: str | None = None, base=base, size=size) -> None:
                        ctx.step(label, base + share * size, detail)

                    if preset["kind"] == "video":
                        made, checked, seconds = self._export_video(
                            ep, layout, item, spec, dst, mix_path=mix_path, talk=talk, kit_dir=kit_dir,
                            work_dir=work_dir, tags=tags, step=step, check=ctx.check)
                    else:
                        made, checked = self._encode_checked(mix_path, dst, spec, tags=tags, layout=layout, label=item["label"],
                                                             step=step, check=ctx.check)
                        seconds = layout.total
                    with self._lock:
                        ep = self._load(ep_id)
                        entry = {
                            "id": f"x{len(ep.get('exports', [])) + 1}",
                            "target": item["target"],
                            "label": item["label"],
                            "file": str(dst),
                            "status": "done",
                            "at": now_iso(),
                            "sample": False,
                            "written": True,
                            "final_length": fmt_duration(seconds),
                            "seconds": round(seconds, 3),
                            "bytes": checked["bytes"],
                            "layout": recipes.describe(layout),
                            "loudness_change_db": made["gain_db"],
                            "check": {"passed": checked["passed"], "sample": False, "checked_at": now_iso(),
                                      "rules": checked["rules"]},
                        }
                        if preset["kind"] == "video":
                            entry["encoder"] = "NVIDIA NVENC" if videolib.nvenc_available() else "x264 (processor)"
                        ep["exports"] = [x for x in ep.get("exports", []) if x["target"] != item["target"]] + [entry]
                        ep["stages"]["export"] = "done"
                        self._save(ep)
                    entries.append(entry)
                extras = self._write_captions(ep_id, layout, plan)
            finally:
                mix_path.unlink(missing_ok=True)
                shutil.rmtree(work_dir, ignore_errors=True)
            failed = [e for e in entries if not e["check"]["passed"]]
            labels = ", ".join(e["label"] for e in entries)
            summary = f"Exported {labels} ({entries[0]['final_length']})." if entries else "Nothing exported."
            summary += " Some checks failed; see the Export tab." if failed else " All checks passed."
            if extras.get("captions_file"):
                summary += " Captions and the chapter list are saved next to it."
            elif extras.get("chapters_file"):
                summary += " The chapter list is saved next to it (no captions: the episode has no transcript)."
            return {"summary": summary, "exports": entries, "extras": extras}
        return work

    def _write_captions(self, ep_id: str, layout: render.Plan, plan: list[dict[str, Any]]) -> dict[str, Any]:
        """Captions (.srt) and the YouTube chapter list (.txt) beside the exports, timed to the finished file
        (the same layout the exports were rendered from)."""
        first = Path(plan[0]["file"])
        stem = first.name.rsplit(" - ", 1)[0]
        out: dict[str, Any] = {"at": now_iso()}
        words = self._transcript(ep_id).get("words", [])
        if words:
            items = captionslib.cues(captionslib.place_words(words, layout.pieces))
            path = self.guard.writable(first.with_name(f"{stem} - Captions.srt"))
            path.write_text(captionslib.srt(items), encoding="utf-8")
            out.update(captions_file=str(path), captions=len(items))
        if layout.chapters:
            path = self.guard.writable(first.with_name(f"{stem} - YouTube chapters.txt"))
            path.write_text(captionslib.chapter_list(layout.chapters), encoding="utf-8")
            out.update(chapters_file=str(path), chapters=len(layout.chapters))
        with self._lock:
            ep = self._load(ep_id)
            ep["extras"] = out
            self._save(ep)
        return out

    def _encode_checked(self, mix_path: Path, dst: Path, spec: dict[str, Any], *, tags: dict[str, str],
                        layout: render.Plan, label: str, step, check) -> tuple[dict[str, Any], dict[str, Any]]:
        """Encode the mix to `dst` at the target loudness, check it, and if the loudness came out a
        little off (the limiter took some away, or encoding pushed a peak over), encode once more."""
        made = render.encode(mix_path, dst, spec, tags=tags, chapters=layout.chapters, total=layout.total, check=check,
                             progress=lambda p, text: step(text, 0.6 * p))
        step(f"Checking {label}", 0.65, "ffprobe and a loudness scan")
        checked = render.check_file(dst, spec, check=check)
        off = spec["loudness"] - checked["loudness"]["integrated"]
        over = checked["loudness"]["true_peak"] - spec["true_peak_max"]
        if abs(off) > 0.3 or over > 0:
            ceiling = made["ceiling_db"] - (over + 0.5 if over > 0 else 0.0)
            made = render.encode(mix_path, dst, spec, tags=tags, chapters=layout.chapters, total=layout.total,
                                 check=check, measured=made["before"], ceiling_db=ceiling,
                                 # a lower limiter: find the gain again; otherwise just correct it
                                 gain_db=None if over > 0 else made["gain_db"] + off,
                                 progress=lambda p, text: step("Fine-tuning the loudness", 0.7 + 0.2 * p))
            step(f"Checking {label} again", 0.92, "ffprobe and a loudness scan")
            checked = render.check_file(dst, spec, check=check)
        return made, checked

    def _export_video(self, ep: dict[str, Any], layout: render.Plan, item: dict[str, Any], spec: dict[str, Any],
                      dst: Path, *, mix_path: Path, talk: float, kit_dir: Path, work_dir: Path, tags: dict[str, str],
                      step, check) -> tuple[dict[str, Any], dict[str, Any], float]:
        """The finished video: 1080p copies of the kit videos (first time only); then the picture is drawn
        while the sound is encoded and checked beside it (AAC at 384 kbps runs at only about 9x real
        time on one processor core, so a 2-hour episode's sound takes about 14 minutes); then the
        sound goes in and the file is checked."""
        work_dir.mkdir(parents=True, exist_ok=True)
        needs = videolib.needed(layout.picture) if layout.picture else {}

        def video_copy(asset_id: str, until: float, progress=None) -> Path:
            if asset_id.startswith("insert:"):  # a video extra: a file of Nick's
                return self._insert_picture(ep["id"], asset_id[7:], check=check, progress=progress)
            return kit.video_copy(asset_id, kit_dir=kit_dir, drive=self.drive, until=until, check=check,
                                  progress=progress)

        for k, (asset_id, until) in enumerate(sorted(needs.items())):
            name = (kit.get_asset(asset_id) or {}).get("name") or next(
                (x["label"] for x in ep.get("inserts") or [] if f"insert:{x['id']}" == asset_id), asset_id)
            step("Preparing the show's videos", 0.02 + 0.13 * k / max(1, len(needs)),
                 f"{name} at 1080p (only the first time; the copy is kept)")
            video_copy(asset_id, until, progress=lambda p, k=k, name=name: step(
                "Preparing the show's videos", 0.02 + 0.13 * (k + p) / max(1, len(needs)),
                f"{name} at 1080p (only the first time)"))
        sound, picture = work_dir / "video-sound.m4a", work_dir / "picture.mp4"
        stop = threading.Event()
        done: dict[str, Any] = {}

        def stop_check() -> None:
            if stop.is_set():
                raise CutzoError("Stopped because drawing the video failed.")
            if check:
                check()

        def encode_sound() -> None:
            try:
                done["made"], done["check"] = self._encode_checked(
                    mix_path, sound, spec, tags=tags, layout=layout, label="the sound",
                    step=lambda *args, **kwargs: None, check=stop_check)
            except BaseException as exc:  # noqa: BLE001 - handed over to the job below
                done["error"] = exc

        worker = threading.Thread(target=encode_sound, name="video-sound", daemon=True)
        worker.start()
        drawing = "Background, montage, title graphic and spectrum, frame by frame (the sound is encoded alongside)"
        step("Drawing the video", 0.15, drawing)
        try:
            videolib.render_video(
                layout, audio_path=None, mix_path=mix_path, talk_lufs=talk, dst=picture, video=spec["video"],
                kit_video=video_copy, title_lines=titles.lines_for(ep), work_dir=work_dir, check=check, faststart=False,
                progress=lambda p: step("Drawing the video", 0.15 + 0.78 * p, drawing))
        except BaseException:
            stop.set()
            raise
        finally:
            if stop.is_set():
                worker.join()
        if worker.is_alive():
            step("Finishing the sound", 0.93, "Encoding the sound for the video")
            try:
                while worker.is_alive():
                    worker.join(0.5)
                    if check:
                        check()
            except BaseException:
                stop.set()
                worker.join()
                raise
        if "error" in done:
            raise done["error"]
        made, sound_check = done["made"], done["check"]
        step("Putting the sound in", 0.95, "Copying the picture and the sound into the finished file")
        videolib.mux(picture, sound, dst, total=layout.total, check=check,
                     progress=lambda p: step("Putting the sound in", 0.95 + 0.02 * p))
        picture.unlink(missing_ok=True)
        step(f"Checking {item['label']}", 0.97, "ffprobe and the sound's loudness")
        checked = videolib.check_video(dst, spec, audio_loudness=sound_check["loudness"],
                                       expected_seconds=layout.total, check=check)
        if not sound_check["passed"]:
            checked["passed"] = False
            checked["rules"] += [r for r in sound_check["rules"] if not r["pass"]]
        return made, checked, layout.total

    def _talk_loudness(self, ep_id: str, check=None) -> float:
        """How loud the cleaned conversation is (LUFS), measured once per build."""
        ep = self._load(ep_id)
        voice = self._ep_dir(ep_id) / "voice.f32"
        cached = (ep.get("voice") or {}).get("talk_lufs")
        if cached is not None and (ep.get("voice") or {}).get("talk_mtime") == int(voice.stat().st_mtime):
            return float(cached)
        measured = ffmpeg.loudness(voice, check=check)["integrated"]
        with self._lock:
            ep = self._load(ep_id)
            ep.setdefault("voice", {})["talk_lufs"] = measured
            ep["voice"]["talk_mtime"] = int(voice.stat().st_mtime)
            self._save(ep)
        return measured

    EXPORT_TYPES = {".mp4": "video/mp4", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
                    ".wav": "audio/wav"}

    def export_file(self, episode: str, target: str) -> tuple[Path, str]:
        """A finished export (by its target, e.g. youtube-1080p) and its media type, to play in the app."""
        ep = self._load(self.resolve_episode(episode))
        x = next((e for e in ep.get("exports", []) if e["target"] == target and e.get("written")), None)
        if not x:
            raise NotFound(f"This episode has no finished {target} export to play.", hint="Export it first.")
        path = Path(x["file"])
        if not self.guard.is_writable(path):  # only Cutzo's own files, never anything else on the PC
            raise NotFound("Cutzo only plays files from its own exports folder.")
        if not path.exists():
            raise NotFound(f"The finished file isn't there any more ({path}).", hint="Export it again.")
        return path, self.EXPORT_TYPES.get(path.suffix.lower(), "application/octet-stream")

    def reveal(self, file: str) -> dict[str, Any]:
        """Show a finished export in File Explorer (only files inside Cutzo's library)."""
        path = Path(str(file).strip().strip('"'))
        if not self.guard.is_writable(path) or not path.exists():
            raise NotFound("Can't show that file; it isn't one of Cutzo's exports.")
        if os.name == "nt":
            import subprocess
            subprocess.Popen(["explorer.exe", "/select,", str(path)])
            return {"shown": str(path)}
        return {"shown": None, "note": "Showing files works on Windows."}

    def _export_work(self, ep_id: str, plan: list[dict[str, Any]]):
        def work(ctx: JobContext) -> dict[str, Any]:
            n = len(plan)
            done_entries = []
            for k, item in enumerate(plan):
                render = 3.5 if item["kind"] == "video" else 1.5
                parts = [(f"Rendering {item['label']}", render), ("Setting loudness", 0.8), (f"Checking {item['label']}", 0.6)]
                span = sum(s for _, s in parts)
                acc = 0.0
                for label, secs in parts:
                    ctx.step(label, (k + acc / span) / n)
                    ctx.sleep(secs)
                    acc += secs
                preset = presets.get_preset(item["target"]) or {}
                with self._lock:
                    ep = self._load(ep_id)
                    final = self._final_duration(ep) or 0
                    entry = {
                        "id": f"x{len(ep.get('exports', [])) + 1}",
                        "target": item["target"],
                        "label": item["label"],
                        "file": item["file"],
                        "status": "done",
                        "at": now_iso(),
                        "sample": True,
                        "written": False,
                        "final_length": fmt_duration(final),
                        "estimated_gb": round(preset.get("gb_per_hour", 0) * final / 3600, 3),
                        "check": self._mock_check(preset),
                    }
                    ep["exports"] = [x for x in ep.get("exports", []) if x["target"] != item["target"]] + [entry]
                    ep["stages"]["export"] = "done"
                    self._save(ep)
                done_entries.append(entry)
            labels = ", ".join(e["label"] for e in done_entries)
            return {"summary": f"Exported {labels} (pretend: no file written in Phase 1). All checks passed.",
                    "exports": done_entries}
        return work

    def _mock_check(self, preset: dict[str, Any]) -> dict[str, Any]:
        return {
            "passed": True,
            "sample": True,
            "checked_at": now_iso(),
            "rules": [{"rule": r["rule"], "expected": r["expected"], "found": "Sample: matches", "pass": True}
                      for r in preset.get("rules", [])],
            "note": "Phase 1 shows the rules. From Phase 2, Cutzo measures the real file (ffprobe and a loudness scan).",
        }

    def check_export(self, file: str | None = None, episode: str | None = None, target: str | None = None,
                     source: str = "you") -> dict[str, Any]:
        if episode:
            ep = self._load(self.resolve_episode(episode))
            entries = ep.get("exports", [])
            if target:
                p = presets.find_preset(target)
                entries = [x for x in entries if p and x["target"] == p["id"]]
            if not entries:
                raise NotFound("No finished export to check for that episode yet.", hint="Export it first.")
            entry = entries[-1]
            self._log_read(source, "check_export", f"checked the {entry['label']} export of {_q(ep['title'], 40)}", ep["id"])
            return {"file": entry["file"], "label": entry["label"], **entry["check"]}
        if not file:
            raise CutzoError("Which file? Give the file path, or the episode and target.")
        wanted = str(file).strip().strip('"')
        for ep in self._all_projects():
            for x in ep.get("exports", []):
                if x["file"].lower() == wanted.lower():
                    self._log_read(source, "check_export", f"checked {Path(wanted).name}", ep["id"])
                    return {"file": x["file"], "label": x["label"], **x["check"]}
        p = Path(wanted)
        if p.is_file():
            self._log_read(source, "check_export", f"checked {p.name}")
            return {"file": str(p), "exists": True, "bytes": p.stat().st_size, "passed": None,
                    "note": "Cutzo can measure real files from Phase 2 (ffprobe and a loudness scan)."}
        raise NotFound(f"Can't find {wanted}.")

    # =========================================================== jobs, log
    def job_status(self, job_id: str, source: str = "you") -> dict[str, Any]:
        job = self.jobs.get(str(job_id).strip())
        job["progress_text"] = f"{int(round(job['progress'] * 100))}%"
        if job["status"] == "done":
            job["summary"] = (job.get("result") or {}).get("summary", "Done.")
        elif job["status"] in ("queued", "running"):
            job["summary"] = f"{job['title']}: {job.get('step') or 'working'} ({job['progress_text']})"
        else:
            job["summary"] = f"{job['title']}: {job['status']}. {job.get('error') or ''}".strip()
        return job

    def list_jobs(self, active_only: bool = False, limit: int = 30) -> list[dict[str, Any]]:
        return self.jobs.list(limit=limit, active_only=active_only)

    def cancel_job(self, job_id: str, source: str = "you") -> dict[str, Any]:
        job = self.jobs.cancel(job_id)
        self._log(source, "cancel_job", f"Asked to stop: {job['title']}", job.get("episode"))
        return job

    def activity_log(self, limit: int = 50, since: int | None = None, episode: str | None = None,
                     include_reads: bool = True) -> dict[str, Any]:
        ep_id = self.resolve_episode(episode) if episode else None
        items = self.activity.recent(limit=limit if include_reads else limit * 4, since=since, episode=ep_id)
        if not include_reads:
            items = [e for e in items if e["action"] != "read"][:limit]
        return {"entries": items, "last_id": self.activity.last_id}

    def _job_finished(self, job: dict[str, Any]) -> None:
        if job["status"] == "done":
            summary = (job.get("result") or {}).get("summary") or f"Finished: {job['title']}"
            self._log("cutzo", f"{job['kind']}_done", summary, job.get("episode"), details={"job": job["id"]})
        elif job["status"] in ("failed", "cancelled", "interrupted"):
            word = {"failed": "Failed", "cancelled": "Stopped", "interrupted": "Interrupted"}[job["status"]]
            text = f"{word}: {job['title']}" + (f". {job['error']}" if job.get("error") else "")
            self._log("cutzo", f"{job['kind']}_{job['status']}", text, job.get("episode"), ok=False,
                      details={"job": job["id"]})

    # ======================================================== maintenance
    def reset_samples(self, source: str = "you") -> dict[str, Any]:
        with self._lock:
            for ep in self._all_projects():
                if ep.get("sample") and not self.jobs.active_for(ep["id"]):
                    self._trash(ep["id"])
            created = []
            for spec in sample.SAMPLE_EPISODES:
                show = showlib.get_show(spec["show"])
                ep_id = spec["id"] if not self._project_path(spec["id"]).exists() else self._unique_id(spec["id"])
                ep = self._new_project(ep_id, show, spec["title"], sample=True)
                ep["test_episode"] = spec["test_episode"]
                ep["script"] = spec["script"]
                ep["media"] = sample.sample_media(spec["media"])
                ep["cameras"] = self._cameras(ep["media"])
                ep["stages"]["import"] = "done"
                self._save(ep)
                if spec["built"]:
                    self._apply_sync(ep_id)
                    self._apply_voice(ep_id, show["id"])
                    self._apply_transcribe(ep_id)
                    self._apply_auto_edit(ep_id)
                    for key, _, _ in BUILD_STEPS:
                        self._set_stage(ep_id, key, "done")
                created.append(ep_id)
            write_json(self.guard.writable(self.library / "samples" / "seeded.json"), {"at": now_iso(), "episodes": created})
        self._log(source, "reset_samples", f"Set up {len(created)} sample episodes for the Phase 1 preview")
        return {"episodes": created}

    def self_test(self, source: str = "you") -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def add(name: str, ok: bool | None, detail: str) -> None:
            checks.append({"check": name, "ok": ok, "detail": detail})

        try:
            probe = self.guard.writable(self.library / "logs" / "self-test.tmp")
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            add("Library folder", True, f"Cutzo can save its work in {self.library}.")
        except OSError as exc:
            add("Library folder", False, f"Can't write to {self.library}: {exc}")
        try:
            self.guard.writable(Path(self.library).parent / "not-cutzo.txt")
            add("Safety rule", False, "Writing outside Cutzo's folders was NOT blocked.")
        except NotAllowed:
            add("Safety rule", True, "Writing outside Cutzo's folders is blocked, so originals stay safe.")
        info = self.info()
        if info["free_gb"] is None:
            add("Free space", None, "Couldn't read the free space.")
        else:
            add("Free space", info["free_gb"] >= 50,
                f"{info['free_gb']:.0f} GB free on the library drive" + ("" if info["free_gb"] >= 50 else " (under 50 GB; episodes need room)."))
        samples_ok = any(ep.get("sample") for ep in self._all_projects())
        add("Sample episodes", samples_ok, "Present." if samples_ok else "Missing. Use Reset sample data in Settings.")
        add("Engine", True, f"Cutzo {__version__} (Phase {PHASE}) on Python {info['python']}.")
        for t in toolbox.status():
            if t["ok"]:
                where = "Cutzo's own copy" if t["own_copy"] else "the copy on this computer's PATH"
                detail = f"Ready ({where}, {t['version'] if t['own_copy'] else 'version not checked'})."
                if t["tool"] == "ffmpeg":
                    found = ffmpeg.version()
                    detail = f"Ready: version {found or 'unknown'} ({where})."
                add(t["label"], True, detail)
            else:
                add(t["label"], False, "Not installed. Double-click “Update Cutzo” in the Cutzo folder to install it.")
        last = self.activity.last_from("claude")
        add("Claude connector", True if last else None,
            f"Last used {last['at']}." if last else "Not used yet. In the Claude app, ask: \u201cList my Cutzo episodes.\u201d")
        passed = sum(1 for c in checks if c["ok"])
        failed = sum(1 for c in checks if c["ok"] is False)
        summary = f"Self-test: {passed} passed, {failed} failed, {len(checks) - passed - failed} for later"
        self._log(source, "self_test", summary, ok=failed == 0)
        return {"passed": failed == 0, "summary": summary, "checks": checks}

    # ============================================================ helpers
    def _log(self, source: str, action: str, summary: str, episode: str | None = None, ok: bool = True,
             details: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.activity.add(source=source, action=action, summary=summary, episode=episode, ok=ok, details=details)

    def _log_read(self, source: str, tool: str, what: str, episode: str | None = None) -> None:
        # Claude's look-ups are logged too (the plan: every connector call is visible),
        # but the app's own screen refreshes are not.
        if source == "claude":
            self.activity.add(source="claude", action="read", summary=f"Claude {what}", episode=episode,
                              details={"tool": tool})

    def log_failure(self, source: str, action: str, message: str, status: int = 400) -> None:
        """Called by the engine when a change was refused, so refusals show in the log too."""
        word = {403: "Blocked", 409: "Paused"}.get(status, "Didn't work")
        self._log(source, action, f"{word}: {message}", ok=False)

    def _ep_dir(self, ep_id: str) -> Path:
        return self.library / "episodes" / ep_id

    def _project_path(self, ep_id: str) -> Path:
        return self._ep_dir(ep_id) / "project.json"

    def _all_projects(self) -> list[dict[str, Any]]:
        root = self.library / "episodes"
        out = []
        for path in sorted(root.glob("*/project.json")):
            data = read_json(path)
            if isinstance(data, dict) and data.get("id"):
                out.append(data)
        return out

    def _load(self, ep_id: str) -> dict[str, Any]:
        data = read_json(self._project_path(ep_id))
        if not isinstance(data, dict):
            raise NotFound(f"No episode called {ep_id!r}.", hint=self._episode_hint())
        return data

    def _save(self, ep: dict[str, Any]) -> None:
        ep["updated"] = now_iso()
        write_json(self.guard.writable(self._project_path(ep["id"])), ep)

    def _transcript(self, ep_id: str) -> dict[str, Any]:
        data = read_json(self._ep_dir(ep_id) / "transcript.json")
        return data if isinstance(data, dict) else {}

    def _trash(self, ep_id: str) -> Path:
        src = self.guard.writable(self._ep_dir(ep_id))
        dest = self.guard.writable(self.library / "trash" / f"{ep_id}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}")
        shutil.move(str(src), str(dest))
        return dest

    def _unique_id(self, base: str) -> str:
        candidate, n = base, 2
        while self._project_path(candidate).exists():
            candidate = f"{base}-{n}"
            n += 1
        return candidate

    def _new_project(self, ep_id: str, show: dict[str, Any], title: str, *, folder: str | None = None,
                     sample: bool = False) -> dict[str, Any]:
        stamp = now_iso()
        return {
            "schema": 1,
            "id": ep_id,
            "show": show["id"],
            "title": title,
            "sample": sample,
            "test_episode": False,
            "created": stamp,
            "updated": stamp,
            "folder": folder,
            "media": [],
            "cameras": [],
            "stages": {key: "todo" for key, _ in STAGES if key != "review"},
            "duration": None,
            "voice": None,
            "edits": [],
            "chapters": [],
            "assets": {},
            "exports": [],
            "history": [],
            "seq": 0,
        }

    def _next_id(self, ep: dict[str, Any], prefix: str) -> str:
        ep["seq"] = int(ep.get("seq", 0)) + 1
        return f"{prefix}{ep['seq']}"

    def _new_edit(self, ep: dict[str, Any], *, type: str, start: float, end: float, by: str, **extra: Any) -> dict[str, Any]:
        edit = {"id": self._next_id(ep, "e"), "type": type, "start": round(float(start), 3), "end": round(float(end), 3),
                "by": by, "at": now_iso()}
        edit.update({k: v for k, v in extra.items() if v is not None})
        return edit

    def _new_chapter(self, ep: dict[str, Any], time: float, title: str, *, by: str) -> dict[str, Any]:
        return {"id": self._next_id(ep, "c"), "time": round(float(time), 3), "title": title, "by": by, "at": now_iso()}

    def _push_history(self, ep: dict[str, Any], item: dict[str, Any]) -> None:
        history = ep.setdefault("history", [])
        history.append(item)
        del history[:-200]

    def _set_stage(self, ep_id: str, key: str, status: str) -> None:
        with self._lock:
            ep = self._load(ep_id)
            ep["stages"][key] = status
            self._save(ep)

    def _refuse_if_busy(self, ep_id: str, starting: str) -> None:
        """One long job per episode at a time (an export during a rebuild would mix old and new)."""
        busy = self.jobs.active_for(ep_id)
        if busy:
            job = busy[0]
            doing = {"build": "building", "export": "exporting", "import": "importing",
                     "transcribe": "transcribing"}.get(job["kind"], "working on")
            raise CutzoError(f"Cutzo is still {doing} this episode (job {job['id']}). Try again when it finishes.",
                             data={"job": job["id"]})

    def _export_path(self, ep: dict[str, Any], show: dict[str, Any], preset: dict[str, Any]) -> str:
        """exports/<Show>/<Title> - <Target>.<ext>; adds the episode id if another episode has that name."""
        out_dir = self.library / "exports" / safe_filename(show.get("name", "Other"))
        label = safe_filename(preset["label"].split(" (")[0])
        path = out_dir / f"{safe_filename(ep['title'])} - {label}.{preset['ext']}"
        taken = {x["file"].lower() for other in self._all_projects() if other["id"] != ep["id"]
                 for x in other.get("exports", [])}
        if str(path).lower() in taken:
            path = out_dir / f"{safe_filename(ep['title'])} ({ep['id']}) - {label}.{preset['ext']}"
        return str(self.guard.writable(path))

    def _require_built(self, ep: dict[str, Any]) -> None:
        if ep["stages"].get("auto_edit") != "done" or not ep.get("duration"):
            raise CutzoError("This episode isn't built yet, so there's nothing to edit.",
                             hint="Build it first (build_episode); it syncs, cleans, transcribes and suggests cuts.")

    def _check_big_cut(self, length: float, duration: float, confirm: bool) -> None:
        if confirm:
            return
        if length > BIG_CUT_SECONDS or (duration and length / duration > BIG_CUT_SHARE and length > 5):
            share = f" ({length / duration:.0%} of the episode)" if duration else ""
            raise NeedsConfirmation(f"That cut removes {fmt_duration(length)}{share}.",
                                    hint="Repeat with confirm=true if that's intended; check with Nick first.")

    def _cuts(self, ep: dict[str, Any]) -> list[tuple[float, float]]:
        return timeline.merge((e["start"], e["end"]) for e in ep.get("edits", []) if e["type"] == "cut")

    def _final_duration(self, ep: dict[str, Any]) -> float | None:
        if not ep.get("duration"):
            return None
        return round(timeline.total(timeline.kept(float(ep["duration"]), self._cuts(ep))), 3)

    def _word_cut_points(self, ep: dict[str, Any], a: float, b: float) -> tuple[float, float]:
        """A cut made by selecting words: move its edges to the quiet between words."""
        words = self._transcript(ep["id"]).get("words", [])
        run = cutpoints.words_in(words, a, b) if words else None
        if not run:
            return a, b
        return cutpoints.word_cut(self._ep_dir(ep["id"]) / "voice.f32", words, run[0], run[1], float(ep["duration"]))

    def _words_between(self, ep_id: str, a: float, b: float) -> str:
        words = self._transcript(ep_id).get("words", [])
        inside = [w["w"] for w in words if a <= tx.anchor(w) < b]
        return " ".join(inside)

    def _match_angle(self, angle: str, cams: list[str]) -> str:
        want = str(angle or "").strip().lower()
        if not want:
            raise CutzoError(f"Which camera? Choose from: {', '.join(cams)}.")
        if want in ANGLE_ALIASES and "Everyone" in cams:
            return "Everyone"
        exact = [c for c in cams if c.lower() == want]
        if exact:
            return exact[0]
        hits = [c for c in cams if want in c.lower()]
        if len(hits) == 1:
            return hits[0]
        raise CutzoError(f"No camera called \u201c{angle}\u201d. Choose from: {', '.join(cams)}.")

    def _match_slot(self, show: dict[str, Any], slot: str) -> dict[str, Any]:
        want = str(slot or "").strip().lower()
        for s in show.get("slots", []):
            if want in (s["slot"], s["label"].lower()):
                return s
        hits = [s for s in show.get("slots", []) if want and (want in s["slot"] or want in s["label"].lower())]
        if len(hits) == 1:
            return hits[0]
        labels = ", ".join(f"{s['label']} ({s['slot']})" for s in show.get("slots", []))
        raise CutzoError(f"{show.get('name', 'This show')} has no part called \u201c{slot}\u201d. Parts: {labels}.")

    def _match_choice(self, sdef: dict[str, Any], asset: str) -> str:
        want = str(asset or "").strip().lower()
        choices = sdef.get("choices", [])
        if want in ("", "off", "nothing", "remove"):
            want = "none"
        for c in choices:
            a = kit.get_asset(c) or {}
            if want in (c, a.get("name", "").lower(), a.get("name", "").lower().rsplit(".", 1)[0]):
                return c
        hits = [c for c in choices if want in c or want in (kit.get_asset(c) or {}).get("name", "").lower()]
        if len(hits) == 1:
            return hits[0]
        names = ", ".join((kit.get_asset(c) or {}).get("name", c) for c in choices)
        raise CutzoError(f"\u201c{asset}\u201d isn't a choice for {sdef['label']}. Choices: {names}.")

    def _cameras(self, media: list[dict[str, Any]]) -> list[str]:
        cams: list[str] = []
        gh5 = 0
        for m in media:
            if m.get("kind") != "video":
                continue
            if m.get("source") == "streamyard-track" and m.get("person"):
                name = m["person"]
            elif m.get("source") in ("streamyard-mix", "mix"):
                name = "Everyone"
            elif m.get("source") == "gh5":
                gh5 += 1
                name = "GH5 camera" if gh5 == 1 else f"GH5 camera {gh5}"
            else:
                name = Path(m["name"]).stem
            if name not in cams:
                cams.append(name)
        if len([c for c in cams if c != "Everyone"]) >= 2 and "Everyone" not in cams:
            cams.append("Everyone")
        return cams

    def _guess_show(self, folder: Path | None) -> dict[str, Any] | None:
        if not folder:
            return None
        text = str(folder).lower()
        if "heretic" in text or "tnh" in text:
            return showlib.get_show("the-new-heretics")
        if "dig" in text or "denver" in text or "stiffs" in text:
            return showlib.get_show("the-dig")
        return None

    def _episode_hint(self) -> str:
        eps = sorted(self._all_projects(), key=lambda e: e["updated"], reverse=True)[:5]
        if not eps:
            return "There are no episodes yet."
        return "Recent episodes: " + "; ".join(f"{e['title']} ({e['id']})" for e in eps)

    # ------------------------------------------------------------ views
    def _stages_view(self, ep: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        st = dict(ep.get("stages", {}))
        built = st.get("auto_edit") == "done"
        st["review"] = "done" if st.get("export") == "done" else ("ready" if built else "todo")
        out, current = [], None
        for key, label in STAGES:
            status = st.get(key, "todo")
            if current is None and status not in ("done", "later"):
                current = key
            out.append({"key": key, "label": label, "status": status})
        return out, current or "done"

    def _summary(self, ep: dict[str, Any]) -> dict[str, Any]:
        show = showlib.get_show(ep["show"]) or {}
        stages, current = self._stages_view(ep)
        final = self._final_duration(ep)
        running = self.jobs.active_for(ep["id"])
        return {
            "id": ep["id"],
            "title": ep["title"],
            "show": ep["show"],
            "show_name": show.get("name", ep["show"]),
            "format": show.get("format"),
            "color": show.get("color"),
            "sample": bool(ep.get("sample")),
            "test_episode": bool(ep.get("test_episode")),
            "stage": current,
            "stage_label": STAGE_LABELS.get(current, current),
            "stages": stages,
            "duration": ep.get("duration"),
            "final_duration": final,
            "length_text": fmt_duration(ep["duration"]) if ep.get("duration") else "Not built yet",
            "final_text": fmt_duration(final) if final is not None else None,
            "cut_count": sum(1 for e in ep.get("edits", []) if e["type"] == "cut"),
            "pause_count": sum(1 for e in ep.get("edits", []) if e["type"] == "cut" and e.get("reason") == "dead air"
                               and e.get("by") == "auto"),
            "chapter_count": len(ep.get("chapters", [])),
            "media_count": len(ep.get("media", [])),
            "export_count": sum(1 for x in ep.get("exports", []) if x["status"] == "done"),
            "needs_rebuild": bool(ep.get("needs_rebuild")),
            "busy": [{"id": j["id"], "kind": j["kind"], "title": j["title"]} for j in running],
            "updated": ep.get("updated"),
            "created": ep.get("created"),
        }

    MARK_WORDS = {"claude", "claud", "clawed", "clod", "klaud", "claudes"}

    def _marks(self, ep: dict[str, Any]) -> list[dict[str, Any]]:
        """Spots where someone said "Claude" while recording: Nick's sign for "Claude, cut around here".
        Each has its recording time, the words around it and whether it already falls inside a cut."""
        words = self._transcript(ep["id"]).get("words", [])
        cuts = self._cuts(ep)
        out: list[dict[str, Any]] = []
        for i, w in enumerate(words):
            if re.sub(r"[^a-z]", "", str(w["w"]).lower()) not in self.MARK_WORDS:
                continue
            t = float(w["s"])
            before = " ".join(x["w"] for x in words[max(0, i - 25):i])
            after = " ".join(x["w"] for x in words[i + 1:i + 26])
            out.append({"time": round(t, 3), "time_text": fmt_time(t), "word": w["w"],
                        "before": before, "after": after,
                        "cut": tx.is_word_cut(w, cuts)})
        return out

    def _media_view(self, ep: dict[str, Any]) -> list[dict[str, Any]]:
        """The recordings, with "part"/"parts" when several combined recordings play one after another."""
        media = [dict(m) for m in ep.get("media", [])]
        mixes = [m for m in media if buildlib.is_mix(m) and not m.get("problem")]
        tracks = [m for m in media if m.get("source") == "streamyard-track" and not m.get("problem")]
        for m in media:
            m.pop("part", None)
        if len(mixes) > 1 and not tracks:
            order =buildlib.join_order([n for n in ep["media"] if buildlib.is_mix(n) and not n.get("problem")])
            place = {n["id"]: k + 1 for k, n in enumerate(order)}
            for m in mixes:
                m["part"], m["parts"] = place[m["id"]], len(order)
        return media

    def _edit_view(self, e: dict[str, Any], ep: dict[str, Any]) -> dict[str, Any]:
        v = dict(e)
        v["start_text"] = fmt_time(e["start"], precise=True)
        v["end_text"] = fmt_time(e["end"], precise=True)
        v["length"] = round(e["end"] - e["start"], 3)
        return v

    def _layout(self, ep: dict[str, Any], show: dict[str, Any]) -> Any:
        """The finished file's layout (intro, breaks, ending) without touching any media: used for
        chapter times and the finished length. None for sample episodes and shows without a recipe."""
        if ep.get("sample") or not ep.get("duration") or not show.get("recipe"):
            return None
        try:
            return recipes.plan_episode(ep, show, voice_path=Path("voice.f32"), talk_lufs=recipes.TALK_REFERENCE_LUFS,
                                        kit_file=lambda asset: Path(asset),
                                        length_of=lambda asset: kit.seconds(asset) or 1e9)
        except CutzoError:
            return None

    def _chapter_view(self, c: dict[str, Any], ep: dict[str, Any], show: dict[str, Any],
                      layout_time: float | None = None) -> dict[str, Any]:
        cuts = self._cuts(ep)
        out_t = layout_time if layout_time is not None else timeline.to_output(c["time"], cuts)
        voiced = showlib.segment_title_for(show, c["title"])
        return {**c, "segment": bool(c.get("segment")), "time_text": fmt_time(c["time"]), "output_time": out_t,
                "output_text": fmt_time(out_t), "voiced_title": voiced["name"] if voiced else None}

    def _view(self, ep: dict[str, Any]) -> dict[str, Any]:
        show = showlib.get_show(ep["show"]) or {}
        view = self._summary(ep)
        layout = self._layout(ep, show)
        ordered = sorted(ep.get("chapters", []), key=lambda c: c["time"])
        chapters = [self._chapter_view(c, ep, show, layout.chapters[k]["time"] if layout else None)
                    for k, c in enumerate(ordered)]
        if chapters:
            chapters[0]["voiced_title"] = None  # the first chapter starts the talk: no title clip there
        if layout:
            view["finished_duration"] = round(layout.total, 3)
            view["finished_text"] = fmt_duration(layout.total)
            view["layout"] = recipes.describe(layout)
        warnings = []
        if chapters:
            if chapters[0]["output_time"] > 0.5:
                warnings.append("YouTube needs the first chapter at 0:00.")
            if len(chapters) < 3:
                warnings.append("YouTube shows chapters only when there are at least 3.")
            gaps = [b["output_time"] - a["output_time"] for a, b in zip(chapters, chapters[1:])]
            if any(g < 10 for g in gaps):
                warnings.append("YouTube needs chapters at least 10 seconds apart (the sample clip is short, so this is expected here).")
        edits = sorted(ep.get("edits", []), key=lambda e: (e["start"], e["id"]))
        view.update({
            "folder": ep.get("folder"),
            "media": self._media_view(ep),
            "cameras": ep.get("cameras", []),
            "voice": ep.get("voice"),
            "edits": [self._edit_view(e, ep) for e in edits],
            "kept": [{"start": a, "end": b} for a, b in timeline.kept(float(ep["duration"]), self._cuts(ep))] if ep.get("duration") else [],
            "chapters": chapters,
            "youtube_chapters": "\n".join(f"{c['output_text']} {c['title']}" for c in chapters),
            "chapter_warnings": warnings,
            "slots": [self._slot_view(show, s, ep.get("assets")) for s in show.get("slots", [])],
            "outputs": show.get("outputs", []),
            "has_picture": show.get("recipe") == "the-new-heretics",
            "breaks_at": show.get("breaks_at", "chapters"),
            "video_title": titles.lines_for(ep) if show.get("recipe") == "the-new-heretics" else None,
            "exports": ep.get("exports", []),
            "extras": ep.get("extras"),
            "teaser": ep.get("teaser"),
            "marks": self._marks(ep),
            "has_transcript": bool(self._transcript(ep["id"]).get("words")),
            "has_original": (self._ep_dir(ep["id"]) / "original.m4a").exists(),
            "audio_type": "audio/mp4" if (self._ep_dir(ep["id"]) / "preview.m4a").exists() else "audio/wav",
            "voice_settings": None if ep.get("sample") else VoiceSettings.from_dict(self._voice_settings(ep, show)).__dict__,
            "dead_air": None if ep.get("sample") else {**buildlib.DEAD_AIR, **(ep.get("dead_air") or {})},
            "build_notes": ep.get("build_notes", []),
            "picks": {key: [self._pick_view(p, key) for p in (ep.get("picks") or {}).get(key, [])]
                      for key in self.PICK_KINDS},
            "inserts": sorted((self._insert_view(x, ep, show) for x in ep.get("inserts") or []),
                              key=lambda v: v["at_time"]),
            "can_undo": bool(ep.get("history")),
            "last_change": (ep.get("history") or [{}])[-1].get("summary"),
            "jobs": self.jobs.list(limit=5, episode=ep["id"]),
            "note": PHASE1_NOTE if ep.get("sample") else REAL_NOTE,
        })
        return view
