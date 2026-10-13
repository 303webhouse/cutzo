"""Cutzo's core: every action the app or Claude can take, as plain methods.

The web engine (cutzo.engine) and the Claude connector (cutzo.mcp_server) are
thin wrappers around this class, so the screens and Claude can never drift apart.

Phase 1: the editing model is real (cuts, chapters, camera choices, undo,
safety rules, activity log), while the heavy media work (sync, voice cleanup,
transcription, rendering) is pretended with timed steps and sample data.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import sys
import threading
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .. import PHASE, __version__
from . import kit, presets, sample, timeline
from . import shows as showlib
from . import transcript as tx
from .activity import ActivityLog
from .errors import CutzoError, NeedsConfirmation, NotAllowed, NotFound
from .jobs import JobContext, JobRunner, now_iso
from .media import classify, kind_of, scan_folder
from .safety import Guard
from .storage import read_json, write_json
from .timecode import fmt_duration, fmt_time, parse_time

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
PHASE1_NOTE = "Phase 1 preview: sample data, nothing is rendered and your real files are never touched."
EXPORT_NOTE = "Phase 1: the export is pretend, so no file is written. Real files start in Phase 2."
BUILD_NOTE = "Phase 1: the steps are pretend and use a short test clip instead of the real recording."
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
    def __init__(self, library: Path | str, *, speed: float = 1.0, seed_samples: bool = True, workers: int = 2):
        self.library = Path(library)
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
            "note": PHASE1_NOTE,
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
            ctx.sleep(1.0)
            ctx.step("Reading recording details", 0.5)
            ctx.sleep(1.0)
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
                    ep["media"].append(entry)
                    known.add(info["path"].lower())
                    added += 1
                ep["cameras"] = self._cameras(ep["media"])
                ep["stages"]["import"] = "done"
                if added and ep["stages"].get("auto_edit") == "done":
                    ep["needs_rebuild"] = True
                self._save(ep)
            return {"added": added, "summary": f"Imported {added} recording(s) into \u201c{ep['title']}\u201d."}
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
        with self._lock:
            self._refuse_if_busy(ep_id, "build")
            job = self.jobs.submit(kind="build", title=f"Build {ep['title']}", episode=ep_id, source=source,
                                   steps=[label for _, label, _ in BUILD_STEPS], work=self._build_work(ep_id, show["id"]))
        self._log(source, "build_episode", f"Started building \u201c{ep['title']}\u201d (template: {show['name']})",
                  ep_id, details={"job": job["id"]})
        return {"job": job, "steps": [label for _, label, _ in BUILD_STEPS], "note": BUILD_NOTE}

    def _build_work(self, ep_id: str, show_id: str):
        def work(ctx: JobContext) -> dict[str, Any]:
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
        voice = show.get("voice", {"preset": "Podcast voice", "strength": 70})
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

    # ========================================================== transcript
    def get_transcript(self, episode: str, include_cut: bool = False, fmt: str = "text",
                       source: str = "you") -> dict[str, Any]:
        ep = self._load(self.resolve_episode(episode))
        tr = self._transcript(ep["id"])
        if not tr.get("words"):
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
            out["text"] = tx.as_text(tr, cuts, include_cut=include_cut)
        return out

    def waveform(self, episode: str, buckets: int = 1200) -> dict[str, Any]:
        ep_id = self.resolve_episode(episode)
        wav = self._ep_dir(ep_id) / "preview.wav"
        if not wav.exists():
            raise NotFound("No audio to draw yet. Build the episode first.")
        cache = self._ep_dir(ep_id) / "waveform.json"
        mtime = wav.stat().st_mtime
        data = read_json(cache)
        if isinstance(data, dict) and data.get("buckets") == buckets and data.get("mtime") == mtime:
            return data
        data = {"buckets": buckets, "mtime": mtime, **sample.waveform_peaks(wav, buckets)}
        write_json(self.guard.writable(cache), data)
        return data

    def preview_audio(self, episode: str) -> Path:
        ep_id = self.resolve_episode(episode)
        wav = self._ep_dir(ep_id) / "preview.wav"
        if not wav.exists():
            raise NotFound("No preview audio yet. Build the episode first.")
        return wav

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
                a, b = tx.span(words, i, j)
                said = " ".join(w["w"] for w in words[i:j])
                made.append(self._new_edit(ep, type="cut", start=round(max(0.0, a - 0.02), 3),
                                           end=round(min(float(ep["duration"]), b + 0.04), 3),
                                           reason="words", text=said, by=source))
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

    def add_chapter(self, episode: str, time: Any, title: str, source: str = "you") -> dict[str, Any]:
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
                summary = f"Renamed the chapter at {fmt_time(near['time'])} to \u201c{title}\u201d"
                self._push_history(ep, {"op": "update_chapter", "before": before, "summary": summary})
                chapter = near
            else:
                chapter = self._new_chapter(ep, t, title, by=source)
                ep["chapters"].append(chapter)
                ep["chapters"].sort(key=lambda c: c["time"])
                summary = f"Added chapter \u201c{title}\u201d at {fmt_time(t)}"
                self._push_history(ep, {"op": "add_chapter", "id": chapter["id"], "summary": summary})
            self._save(ep)
        self._log(source, "add_chapter", f"{summary} in {_q(ep['title'], 40)}", ep["id"])
        show = showlib.get_show(ep["show"]) or {}
        voiced = showlib.segment_title_for(show, title)
        return {"chapter": self._chapter_view(chapter, ep, show), "summary": summary,
                "voiced_title": voiced["name"] if voiced else None}

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
        with self._lock:
            self._refuse_if_busy(ep_id, "export")
            job = self.jobs.submit(kind="export", title=f"Export {ep['title']}", episode=ep_id, source=source,
                                   steps=[f"Render {p['label']}" for p in chosen], work=self._export_work(ep_id, plan))
        labels = ", ".join(p["label"] for p in chosen)
        self._log(source, "export", f"Started exporting {labels} for {_q(ep['title'], 40)}", ep_id,
                  details={"job": job["id"]})
        return {"job": job, "targets": plan, "note": EXPORT_NOTE}

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
        ffmpeg = shutil.which("ffmpeg")
        add("FFmpeg", None, f"Found at {ffmpeg}." if ffmpeg else "Not installed yet; it isn't needed until Phase 2.")
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
            doing = {"build": "building", "export": "exporting", "import": "importing"}.get(job["kind"], "working on")
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

    def _words_between(self, ep_id: str, a: float, b: float) -> str:
        words = self._transcript(ep_id).get("words", [])
        inside = [w["w"] for w in words if a <= (w["s"] + w["e"]) / 2 < b]
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
            if current is None and status != "done":
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
            "chapter_count": len(ep.get("chapters", [])),
            "media_count": len(ep.get("media", [])),
            "export_count": sum(1 for x in ep.get("exports", []) if x["status"] == "done"),
            "needs_rebuild": bool(ep.get("needs_rebuild")),
            "busy": [{"id": j["id"], "kind": j["kind"], "title": j["title"]} for j in running],
            "updated": ep.get("updated"),
            "created": ep.get("created"),
        }

    def _edit_view(self, e: dict[str, Any], ep: dict[str, Any]) -> dict[str, Any]:
        v = dict(e)
        v["start_text"] = fmt_time(e["start"], precise=True)
        v["end_text"] = fmt_time(e["end"], precise=True)
        v["length"] = round(e["end"] - e["start"], 3)
        return v

    def _chapter_view(self, c: dict[str, Any], ep: dict[str, Any], show: dict[str, Any]) -> dict[str, Any]:
        cuts = self._cuts(ep)
        out_t = timeline.to_output(c["time"], cuts)
        voiced = showlib.segment_title_for(show, c["title"])
        return {**c, "time_text": fmt_time(c["time"]), "output_time": out_t, "output_text": fmt_time(out_t),
                "voiced_title": voiced["name"] if voiced else None}

    def _view(self, ep: dict[str, Any]) -> dict[str, Any]:
        show = showlib.get_show(ep["show"]) or {}
        view = self._summary(ep)
        chapters = [self._chapter_view(c, ep, show) for c in sorted(ep.get("chapters", []), key=lambda c: c["time"])]
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
            "media": ep.get("media", []),
            "cameras": ep.get("cameras", []),
            "voice": ep.get("voice"),
            "edits": [self._edit_view(e, ep) for e in edits],
            "kept": [{"start": a, "end": b} for a, b in timeline.kept(float(ep["duration"]), self._cuts(ep))] if ep.get("duration") else [],
            "chapters": chapters,
            "youtube_chapters": "\n".join(f"{c['output_text']} {c['title']}" for c in chapters),
            "chapter_warnings": warnings,
            "slots": [self._slot_view(show, s, ep.get("assets")) for s in show.get("slots", [])],
            "outputs": show.get("outputs", []),
            "exports": ep.get("exports", []),
            "has_transcript": bool(self._transcript(ep["id"]).get("words")),
            "can_undo": bool(ep.get("history")),
            "last_change": (ep.get("history") or [{}])[-1].get("summary"),
            "jobs": self.jobs.list(limit=5, episode=ep["id"]),
            "note": PHASE1_NOTE if ep.get("sample") else BUILD_NOTE,
        })
        return view
