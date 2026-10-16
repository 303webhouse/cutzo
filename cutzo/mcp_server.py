"""Cutzo's Claude connector: an MCP server the Claude desktop app starts over stdio.

Every tool calls Cutzo's engine on this computer (http://127.0.0.1:8765), so
changes show up live in the Cutzo window and in its activity log as "Claude".
If the engine isn't running, the connector starts it in the background.

Run by hand (for testing):  python -m cutzo.mcp_server
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Annotated, Any, Union

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from . import PHASE, __version__
from .config import LOCAL, SettingsError, load_settings
from .starter import start_engine, tail

INSTRUCTIONS = f"""Cutzo is Nick's own podcast and video editor, running on his Windows PC.
Shows: The Dig (audio podcast, Denver Stiffs) and The New Heretics (video podcast with a fire
background, audio spectrum and opening montage).

This is Phase {PHASE}. Episodes marked sample are pretend data. Real episodes are built for real:
Cutzo lines up the recordings, cleans the voices (noise removal, de-essing, leveling), writes down
every word with its time (speech to text, fillers like "um" included) and finds dead air; export
writes real files checked against each platform's rules: the podcast MP3 and, for The New Heretics,
the 1080p YouTube video.

How to work:
- Start with list_episodes, then get_episode and get_transcript. A 90-minute transcript is about
  15,000 words: read it in stretches (start/end) when you only need part. An episode built before
  speech to text was installed has no transcript: transcribe_episode makes one.
- Cut by words with cut_words (exact words from the transcript; Cutzo cuts in the quiet between
  words), or by time with cut_section. Episodes can be named loosely ("spooky", "debrief part 2").
- Times are in the ORIGINAL recording, written like 12:03 or 1:02:45.5. Cutzo works out the finished
  file's times itself (intro, breaks and cuts included).
- The Dig's finished file follows Nick's own layout: a cold open of highlights, the theme and sound
  bites, the conversation with a theme break and stinger before each new chapter, then sign-off clips.
  add_highlight picks the cold-open and sign-off stretches; chapters mark where segments start.
- The New Heretics follows Nick's Spooky Sode edit: a teaser (add_highlight 'cold open') that ends as
  the opening montage's music starts, the montage, the talk over the background loop with the episode
  title graphic (set_title_graphic) and the audio spectrum, then the ending music and Full Flames under
  the sign-off picks. Every chapter is a new topic (a page flip in a short pause); chapters marked as
  a new segment (add_chapter new_segment=true, or mark_segment) get a music break before them instead.
- Extras (add_extra) are one-off sounds or videos for one episode, like the spooky haiku in Spooky
  Sode's first break: a file on Nick's PC or a show-kit item, at a time in the recording (the talk
  pauses there) or at a chapter (it plays in that chapter's break, before the page flip or stinger).
- Long jobs (import_media, build_episode, transcribe_episode, export) return a job id at once; poll
  job_status. Building a 90-minute episode takes about 15 minutes, transcribing alone about 4.
- Every change is logged as "Claude" and shows up live in Cutzo's window. undo_last_change
  reverses the latest change; restore_cut brings back one cut by its id.
- Safety: Cutzo never touches original recordings; it writes only in its own folders. Very
  long cuts and replacing a finished export ask for confirm=true: check with Nick first.
- Nick doesn't code. Describe results in plain English (what changed, how long the episode is
  now), not JSON or ids, unless he asks.
"""

server = MCPServer(name="Cutzo", title="Cutzo", instructions=INSTRUCTIONS, version=__version__)

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
CHANGE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

Episode = Annotated[str, Field(description="Episode id or part of its title, e.g. 'spooky' or 'season debrief part 2'.")]
Time = Annotated[Union[str, float], Field(description="Time in the original recording: '12:03', '1:02:45.5', or seconds.")]


# ---------------------------------------------------------------- engine
class Engine:
    """Talks to Cutzo's engine on localhost; starts it if needed."""

    def __init__(self) -> None:
        self.problem: str | None = None
        try:
            self.settings = load_settings()
        except SettingsError as exc:  # reported on the first tool call, in plain words
            self.problem = str(exc)
            self.settings = None
        self.base = self.settings.base_url if self.settings else "http://127.0.0.1:8765"
        self._lock = threading.Lock()

    def healthy(self, timeout: float = 1.0) -> bool:
        try:
            with LOCAL.open(f"{self.base}/api/health", timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8")).get("app") == "cutzo"
        except Exception:
            return False

    def ensure_running(self) -> None:
        if self.problem:
            raise ToolError(f"Cutzo can't start: {self.problem}")
        if self.healthy():
            return
        # Tool calls can arrive at the same time; only one of them starts the engine.
        with self._lock:
            if self.healthy():
                return
            proc = self.start()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self.healthy():
                    return
                if proc is not None and proc.poll() is not None:
                    if self.healthy(timeout=3):  # another copy won the race; that's fine
                        return
                    break
                time.sleep(0.3)
        logs = self.settings.library / "logs"
        detail = tail(logs / "engine.log", 4, " | ") or tail(logs / "engine-start.log", 4, " | ")
        raise ToolError("Cutzo's engine didn't start. Ask Nick to open Cutzo from its desktop shortcut. "
                        f"Last lines of its log ({logs}): {detail or 'none'}")

    def start(self) -> subprocess.Popen | None:
        return start_engine(self.settings.library)

    def call(self, method: str, path: str, body: dict[str, Any] | None = None,
             query: dict[str, Any] | None = None, timeout: float = 60.0) -> Any:
        self.ensure_running()
        url = self.base + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "X-Cutzo-Source": "claude", "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with LOCAL.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = {}
            message = payload.get("error") or f"Cutzo answered with error {exc.code}."
            if payload.get("hint"):
                message += f" ({payload['hint']})"
            raise ToolError(message) from None
        except urllib.error.URLError as exc:
            raise ToolError(f"Couldn't reach Cutzo at {self.base} ({exc.reason}). "
                            "Open Cutzo from its desktop shortcut and try again.") from None


engine = Engine()


def _ep(episode: str) -> str:
    return "/api/episodes/" + urllib.parse.quote(str(episode).strip(), safe="")


def _job_brief(job: dict[str, Any]) -> dict[str, Any]:
    return {k: job.get(k) for k in ("id", "title", "status", "step", "progress", "error") if job.get(k) is not None}


def _episode_brief(e: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": e["id"],
        "title": e["title"],
        "show": e["show_name"],
        "stage": e["stage_label"],
        "length": e["length_text"],
        "final_length": e.get("final_text"),
        "cuts": e["cut_count"] - e.get("pause_count", 0),
        "pauses_shortened": e.get("pause_count") or None,
        "chapters": e["chapter_count"],
        "exports": e["export_count"],
    }
    if e.get("sample"):
        out["sample"] = True
    if e.get("test_episode"):
        out["test_episode"] = True
    if e.get("busy"):
        out["running"] = [j["title"] for j in e["busy"]]
    if e.get("needs_rebuild"):
        out["needs_rebuild"] = True
    return out


# ------------------------------------------------------------------ tools
@server.tool(annotations=READ, structured_output=False)
def list_shows() -> dict[str, Any]:
    """List Nick's shows with their templates (which intro, background, music etc. every episode gets)."""
    shows = engine.call("GET", "/api/shows")
    return {"shows": [{
        "id": s["id"], "name": s["name"], "format": s["format"], "summary": s["summary"],
        "episodes": s["episode_count"],
        "exports_by_default": [o["label"] for o in s["outputs"] if o],
        "template": {slot["label"]: (slot["asset"] or {}).get("name") for slot in s["slots"]},
    } for s in shows]}


@server.tool(annotations=READ, structured_output=False)
def list_episodes(show: Annotated[str | None, Field(description="Optional: 'The Dig' or 'The New Heretics'.")] = None) -> dict[str, Any]:
    """List episodes, newest first, with their stage (Import, Sync, ... Review, Export) and length."""
    eps = engine.call("GET", "/api/episodes", query={"show": show})
    return {"episodes": [_episode_brief(e) for e in eps]}


@server.tool(annotations=READ, structured_output=False)
def get_episode(
    episode: Episode,
    dead_air_details: Annotated[bool, Field(description="List every automatic dead-air cut (there can be hundreds). "
                                                        "Leave false to get just their count.")] = False,
) -> dict[str, Any]:
    """Everything about one episode: recordings, stages, cuts (with ids), chapters, show-kit choices, exports.
    Automatic dead-air cuts are summarized unless dead_air_details is true."""
    e = engine.call("GET", _ep(episode))
    out = _episode_brief(e)
    cuts = [x for x in e["edits"] if x["type"] == "cut"]
    auto = [x for x in cuts if x.get("reason") == "dead air" and x.get("by") == "auto"]
    shown = cuts if dead_air_details else [x for x in cuts if x not in auto]
    out.update({
        "stages": ", ".join(f"{s['label']}: {s['status']}" for s in e["stages"]),
        "recordings": [{"name": m["name"], "kind": m.get("kind"), "person": m.get("person"),
                        "source": m.get("source_label"), "sync": (m.get("sync") or {}).get("offset")}
                       for m in e["media"]],
        "cameras": e.get("cameras") or None,
        "cuts": [{"id": x["id"], "from": x["start_text"], "to": x["end_text"], "why": x.get("reason"),
                  "words": x.get("text"), "by": x.get("by")} for x in shown],
        "dead_air_cuts": ({"count": len(auto),
                           "total_seconds": round(sum(x["end"] - x["start"] for x in auto), 1),
                           "note": "Listed in 'cuts'." if dead_air_details else
                           "Not listed; call get_episode with dead_air_details=true to see them."}
                          if auto else None),
        "camera_choices": [{"id": x["id"], "from": x["start_text"], "to": x["end_text"], "angle": x.get("angle"),
                            "by": x.get("by")} for x in e["edits"] if x["type"] == "camera"] or None,
        "chapters": [{"id": c["id"], "at": c["time_text"], "title": c["title"], "in_finished_file": c["output_text"],
                      "voiced_title": c.get("voiced_title"), "new_segment": c.get("segment") or None}
                     for c in e["chapters"]],
        "title_graphic": e.get("video_title"),
        "youtube_chapters": e.get("youtube_chapters") or None,
        "chapter_warnings": e.get("chapter_warnings") or None,
        "show_kit": {s["label"]: (s["asset"] or {}).get("name") for s in e["slots"]},
        "exports": [{"label": x["label"], "file": x["file"], "status": x["status"], "written": x.get("written"),
                     "length": x.get("final_length"),
                     "checks": (None if (x.get("check") or {}).get("sample") else
                                ("all passed" if x["check"]["passed"] else
                                 [f"{r['rule']}: expected {r['expected']}, found {r['found']}"
                                  for r in x["check"]["rules"] if not r["pass"]]))}
                    for x in e["exports"]] or None,
        "captions_file": (e.get("extras") or {}).get("captions_file"),
        "chapter_list_file": (e.get("extras") or {}).get("chapters_file"),
        "voice_cleanup": ({"strength": e["voice_settings"]["strength"], "steps": (e.get("voice") or {}).get("steps")}
                          if e.get("voice_settings") else None),
        "dead_air": ({"silences_longer_than": e["dead_air"]["longer_than"], "shrink_to": e["dead_air"]["keep"]}
                     if e.get("dead_air") else None),
        "cold_open": [{"id": p["id"], "from": p["start_text"], "to": p["end_text"]}
                      for p in (e.get("picks") or {}).get("cold_open", [])] or None,
        "sign_off": [{"id": p["id"], "from": p["start_text"], "to": p["end_text"]}
                     for p in (e.get("picks") or {}).get("outro", [])] or None,
        "extras": [{"id": x["id"], "what": x["label"], "kind": x["kind_label"], "where": x["where"],
                    "length": x["length_text"], "part_of_file": x.get("part"), "source": x["source"],
                    "level_change_db": x.get("gain_db") or None} for x in e.get("inserts") or []] or None,
        "build_notes": e.get("build_notes") or None,
        "last_change": e.get("last_change"),
        "note": e.get("note"),
    })
    return {k: v for k, v in out.items() if v is not None}


@server.tool(annotations=CHANGE, structured_output=False)
def create_episode(
    show: Annotated[str | None, Field(description="'The Dig' or 'The New Heretics'. Can be left out if the folder name says.")] = None,
    title: Annotated[str | None, Field(description="Episode title. Defaults to the folder name.")] = None,
    folder: Annotated[str | None, Field(description="Folder with the raw recordings, e.g. 'F:\\Recordings\\TNH 12'. Its recordings get imported.")] = None,
) -> dict[str, Any]:
    """Create a new episode, optionally from a folder of recordings (which starts an import job)."""
    res = engine.call("POST", "/api/episodes", {"show": show, "title": title, "folder": folder})
    out: dict[str, Any] = {"created": _episode_brief(res["episode"])}
    if res.get("import"):
        out["import_job"] = _job_brief(res["import"]["job"])
        out["recordings_found"] = res["import"]["files"]
    if res.get("note"):
        out["note"] = res["note"]
    return out


@server.tool(annotations=CHANGE, structured_output=False)
def import_media(
    episode: Episode,
    paths: Annotated[list[str], Field(description="Full paths of recordings or of folders containing them.")],
) -> dict[str, Any]:
    """Add recordings to an episode (read-only: originals are never changed). Returns a job id to poll."""
    res = engine.call("POST", _ep(episode) + "/import", {"paths": paths})
    return {"job": _job_brief(res["job"]), "files": res["files"], "skipped": res.get("skipped") or None}


@server.tool(annotations=CHANGE, structured_output=False)
def build_episode(
    episode: Episode,
    template: Annotated[str | None, Field(description="Optional show template to use; defaults to the episode's show.")] = None,
) -> dict[str, Any]:
    """Build an episode: line up the recordings, clean the voices, write down every word and suggest
    dead-air cuts (a 90-minute episode takes about 15 minutes). Sample episodes run pretend steps.
    Returns a job id."""
    res = engine.call("POST", _ep(episode) + "/build", {"template": template})
    return {"job": _job_brief(res["job"]), "steps": res["steps"], "note": res.get("note")}


@server.tool(annotations=READ, structured_output=False)
def job_status(job_id: Annotated[str, Field(description="The job id returned when the job started.")]) -> dict[str, Any]:
    """Progress of a long job (import, build, export). Poll every few seconds until status is done or failed."""
    job = engine.call("GET", "/api/jobs/" + urllib.parse.quote(job_id.strip(), safe=""))
    out = _job_brief(job)
    out["summary"] = job.get("summary")
    return out


@server.tool(annotations=READ, structured_output=False)
def get_transcript(
    episode: Episode,
    include_cut: Annotated[bool, Field(description="Also show words already cut, marked [cut: ...].")] = False,
    start: Annotated[Union[str, float, None], Field(description="Optional: only from this recording time, e.g. '30:00'.")] = None,
    end: Annotated[Union[str, float, None], Field(description="Optional: only up to this recording time, e.g. '45:00'.")] = None,
) -> str:
    """The episode's transcript with [m:ss] times per paragraph (original recording time). Use it to find
    what to cut, to place chapters, and to write show notes in the chat. A 90-minute episode is about
    15,000 words: read it in stretches with start/end when you only need part of it."""
    query = {"include_cut": str(include_cut).lower()}
    if start not in (None, ""):
        query["start"] = str(start)
    if end not in (None, ""):
        query["end"] = str(end)
    t = engine.call("GET", _ep(episode) + "/transcript", query=query)
    speakers = ", ".join(t["speakers"]) or "not told apart (one combined recording)"
    head = (f"{t['title']} ({t['show']}). Speakers: {speakers}. "
            f"Recording {t['recording_length']}, finished {t['final_length']}."
            + (f" Showing {t['stretch']}." if t.get("stretch") else "") + f"\n{t['note']}\n\n")
    return head + t["text"]


@server.tool(annotations=CHANGE, structured_output=False)
def transcribe_episode(episode: Episode) -> dict[str, Any]:
    """Write down every word of a built episode with its time (speech to text on Nick's graphics card,
    about 2 minutes for 90 minutes; "um"s and "uh"s included). Building does this too; use this for
    episodes built before, or after a rebuild. Returns a job id to poll with job_status."""
    res = engine.call("POST", _ep(episode) + "/transcribe", {})
    return {"job": _job_brief(res["job"]), "note": res.get("note")}


@server.tool(annotations=CHANGE, structured_output=False)
def cut_section(
    episode: Episode, start: Time, end: Time,
    confirm: Annotated[bool, Field(description="Needed only for very long cuts (over 10 min or a quarter of the episode).")] = False,
) -> dict[str, Any]:
    """Cut out everything between two times (original recording time). Reversible with undo_last_change."""
    res = engine.call("POST", _ep(episode) + "/cuts", {"start": start, "end": end, "confirm": confirm})
    return {"done": res["summary"], "cut_id": res["edit"]["id"], "episode_length_now": res["final_length"]}


@server.tool(annotations=CHANGE, structured_output=False)
def cut_words(
    episode: Episode,
    text: Annotated[str, Field(description="The exact words to remove, as they appear in the transcript.")],
    occurrence: Annotated[Union[int, str, None], Field(description="If the words are said more than once: 1, 2, ... or 'all'.")] = None,
    confirm: Annotated[bool, Field(description="Needed only for very long cuts.")] = False,
) -> dict[str, Any]:
    """Cut specific words or a sentence by quoting them from the transcript."""
    res = engine.call("POST", _ep(episode) + "/cut-words", {"text": text, "occurrence": occurrence, "confirm": confirm})
    return {"done": res["summary"], "cut_ids": [e["id"] for e in res["edits"]], "episode_length_now": res["final_length"]}


@server.tool(annotations=CHANGE, structured_output=False)
def restore_cut(
    episode: Episode,
    cut_id: Annotated[str, Field(description="The cut's id from get_episode, like 'e7'. Also removes a camera choice.")],
) -> dict[str, Any]:
    """Bring back one cut section (or remove one camera choice) by its id."""
    res = engine.call("DELETE", _ep(episode) + "/edits/" + urllib.parse.quote(cut_id.strip(), safe=""))
    return {"done": res["summary"], "episode_length_now": res["final_length"]}


@server.tool(annotations=CHANGE, structured_output=False)
def undo_last_change(episode: Episode) -> dict[str, Any]:
    """Undo the most recent change to this episode (a cut, chapter, camera choice or show-kit swap)."""
    res = engine.call("POST", _ep(episode) + "/undo", {})
    return {"done": res["summary"], "episode_length_now": res["final_length"]}


@server.tool(annotations=CHANGE, structured_output=False)
def set_camera(
    episode: Episode, start: Time, end: Time,
    angle: Annotated[str, Field(description="A camera from get_episode's cameras, e.g. a person's name or 'Everyone'.")],
) -> dict[str, Any]:
    """Choose which camera shows between two times (video episodes with camera recordings only)."""
    res = engine.call("POST", _ep(episode) + "/camera", {"start": start, "end": end, "angle": angle})
    return {"done": res["summary"], "id": res["edit"]["id"]}


@server.tool(annotations=CHANGE, structured_output=False)
def add_chapter(
    episode: Episode, time: Time,
    title: Annotated[str, Field(description="Chapter title (YouTube shows it).")],
    new_segment: Annotated[bool | None, Field(description="The New Heretics: true if this chapter starts a new segment "
                                                          "(a music break plays before it). Leave out for a new topic.")] = None,
) -> dict[str, Any]:
    """Add a chapter mark (or rename the one within a second of that time)."""
    res = engine.call("POST", _ep(episode) + "/chapters", {"time": time, "title": title, "segment": new_segment})
    out = {"done": res["summary"], "in_finished_file": res["chapter"]["output_text"]}
    if res.get("voiced_title"):
        out["voiced_title"] = f"Plays {res['voiced_title']} at this chapter."
    return out


@server.tool(annotations=CHANGE, structured_output=False)
def mark_segment(
    episode: Episode,
    chapter: Annotated[str, Field(description="The chapter's id (from get_episode).")],
    starts_segment: Annotated[bool, Field(description="True: a music break plays before this chapter. "
                                                      "False: it's just a new topic (page flip only).")] = True,
) -> dict[str, Any]:
    """The New Heretics: say whether a chapter starts a new segment, with a music break before it."""
    res = engine.call("POST", _ep(episode) + f"/chapters/{chapter}/segment", {"segment": starts_segment})
    return {"done": res["summary"], "in_finished_file": res["chapter"]["output_text"]}


@server.tool(annotations=CHANGE, structured_output=False)
def set_title_graphic(
    episode: Episode,
    label: Annotated[str | None, Field(description="Top line in bold capitals, e.g. 'Halloween Special:'. '' removes it.")] = None,
    title: Annotated[str | None, Field(description="The episode title, shown in capitals, e.g. 'JW Ghost Stories'.")] = None,
    guests: Annotated[str | None, Field(description="Small line under it, e.g. 'ft. Two exJWs'. '' removes it.")] = None,
) -> dict[str, Any]:
    """The New Heretics: the episode title graphic shown under the show's name during the talk.
    Lines left out stay as they are; by default Cutzo splits the episode title at its colon."""
    res = engine.call("POST", _ep(episode) + "/video-title", {"label": label, "title": title, "guests": guests})
    return {"done": res["summary"]}


@server.tool(annotations=CHANGE, structured_output=False)
def swap_asset(
    episode: Episode,
    slot: Annotated[str, Field(description="Part of the show template, e.g. 'background', 'intro', 'music', 'spectrum'.")],
    asset: Annotated[str, Field(description="Kit file name or id from list_assets, or 'none' to leave the part out.")],
) -> dict[str, Any]:
    """Change one show-kit part for this episode only (for example a different background loop)."""
    res = engine.call("POST", _ep(episode) + "/assets", {"slot": slot, "asset": asset})
    return {"done": res["summary"]}


@server.tool(annotations=CHANGE, structured_output=False)
def add_highlight(
    episode: Episode,
    kind: Annotated[str, Field(description="'cold open' (highlights that open the episode) or 'sign-off' (clips at the very end).")],
    start: Time, end: Time,
) -> dict[str, Any]:
    """Pick a stretch of the recording for the cold open or the sign-off (The Dig layout). Order = order added."""
    res = engine.call("POST", _ep(episode) + "/picks", {"kind": kind, "start": start, "end": end})
    return {"done": res["summary"], "id": res["pick"]["id"], "total_now": res["total"]}


@server.tool(annotations=CHANGE, structured_output=False)
def remove_highlight(
    episode: Episode,
    highlight_id: Annotated[str, Field(description="The id from get_episode's cold_open or sign_off list, like 'p4'.")],
) -> dict[str, Any]:
    """Take a stretch out of the cold open or sign-off."""
    res = engine.call("DELETE", _ep(episode) + "/picks/" + urllib.parse.quote(highlight_id.strip(), safe=""))
    return {"done": res["summary"]}


@server.tool(annotations=CHANGE, structured_output=False)
def add_extra(
    episode: Episode,
    file: Annotated[str, Field(description="Full path of a sound or video file on Nick's PC (e.g. 'G:\\My Drive\\The New "
                                           "Heretics\\Podcast\\Episodes\\Spooky Sode\\Spooky Haiku.wav'), or a show-kit "
                                           "item's name or id from list_assets (e.g. 'Haiku Intro', '93696').")],
    at: Annotated[Union[str, float, None], Field(description="Where the talk pauses for it: a time in the ORIGINAL "
                                                             "recording, like '41:20'. Leave out when giving a chapter.")] = None,
    chapter: Annotated[str | None, Field(description="Or: a chapter's id or title. The extra then plays in that chapter's "
                                                     "pause or music break, after the break music, before the page flip "
                                                     "or stinger.")] = None,
    kind: Annotated[str | None, Field(description="'spoken' (a voice: as loud as the talk, the spectrum shows it), "
                                                  "'music' (a bit below the talk, break visuals on The New Heretics), or "
                                                  "'video' (its picture fills the frame). Left out: Cutzo guesses from the file.")] = None,
    start: Annotated[Union[str, float, None], Field(description="Use only part of the file: from this time in the FILE "
                                                                "(seconds or '0:03.4').")] = None,
    end: Annotated[Union[str, float, None], Field(description="...to this time in the file.")] = None,
    level_db: Annotated[float | None, Field(description="Make it louder or softer than Cutzo's automatic level, in dB "
                                                        "(-20 to 20).")] = None,
    label: Annotated[str | None, Field(description="A short name for it (defaults to the file's name).")] = None,
) -> dict[str, Any]:
    """Add an extra: a one-off sound or video for this episode only (like the spooky haiku in Spooky Sode's
    first break). The talk pauses where it goes; several extras at the same spot play one after another,
    in the order added. Remove with remove_extra; undo_last_change works too."""
    res = engine.call("POST", _ep(episode) + "/extras", {"file": file, "time": at, "chapter": chapter, "kind": kind,
                                                         "start": start, "end": end, "gain_db": level_db, "label": label})
    out = {"done": res["summary"], "id": res["extra"]["id"]}
    if res.get("finished_length"):
        out["finished_length_now"] = res["finished_length"]
    return out


@server.tool(annotations=CHANGE, structured_output=False)
def remove_extra(
    episode: Episode,
    extra_id: Annotated[str, Field(description="The id from get_episode's extras list, like 'i12'.")],
) -> dict[str, Any]:
    """Take an extra out of the episode."""
    res = engine.call("DELETE", _ep(episode) + "/extras/" + urllib.parse.quote(extra_id.strip(), safe=""))
    return {"done": res["summary"]}


@server.tool(annotations=CHANGE, structured_output=False)
def set_dead_air(
    episode: Episode,
    longer_than: Annotated[float | None, Field(description="Silences longer than this many seconds get shortened (default 1.0).")] = None,
    keep: Annotated[float | None, Field(description="...down to this many seconds (default 0.5). Smaller = tighter.")] = None,
) -> dict[str, Any]:
    """How tight the dead-air trimming is (real episodes). Re-suggests the dead-air cuts at once; cuts Nick
    brought back stay back."""
    res = engine.call("POST", _ep(episode) + "/dead-air", {"longer_than": longer_than, "keep": keep})
    return {"done": res["summary"], "episode_length_now": res["final_length"]}


@server.tool(annotations=CHANGE, structured_output=False)
def set_voice_strength(
    episode: Episode,
    strength: Annotated[int, Field(description="0 = no noise removal, 100 = strongest. Both shows default to 25 (light), which Nick picked in a listening test.", ge=0, le=100)],
) -> dict[str, Any]:
    """How strongly this episode's voices are cleaned. Takes effect when the episode is built again
    (build_episode)."""
    res = engine.call("POST", _ep(episode) + "/voice", {"strength": strength})
    return {"done": res["summary"], "rebuild_needed": res["needs_rebuild"]}


@server.tool(annotations=READ, structured_output=False)
def list_assets(show: Annotated[str | None, Field(description="Optional show name to filter by.")] = None) -> dict[str, Any]:
    """The show kit: backgrounds, intros, music, voiced segment titles, sounds and art, and where each is used."""
    res = engine.call("GET", "/api/assets", query={"show": show})
    return {"assets": [{"id": a["id"], "name": a["name"], "use": a["use"], "folder": a["folder"],
                        "used_by": a.get("used_by") or None} for a in res["assets"]], "note": res["note"]}


@server.tool(annotations=CHANGE, structured_output=False)
def export(
    episode: Episode,
    targets: Annotated[list[str] | None, Field(description="Any of: youtube-1080p, substack-video, podcast-mp3, podcast-aac. Default: the show's usual exports.")] = None,
    confirm: Annotated[bool, Field(description="Set true only to replace an export that already finished (ask Nick first).")] = False,
) -> dict[str, Any]:
    """Render the finished files and check each against its platform's rules. Returns a job id to poll.
    Every export also saves captions (.srt, from the transcript, timed to the finished file) and the
    YouTube chapter list (.txt) next to the files. The New Heretics' 1080p video takes a while: the
    first time, Cutzo also makes 1080p copies of the show's videos (kept for next time); a 2-hour
    episode then takes roughly 20-40 minutes."""
    res = engine.call("POST", _ep(episode) + "/export", {"targets": targets, "confirm": confirm})
    return {"job": _job_brief(res["job"]), "files": [t["file"] for t in res["targets"]], "note": res["note"]}


@server.tool(annotations=READ, structured_output=False)
def check_export(
    file: Annotated[str | None, Field(description="Full path of an exported file.")] = None,
    episode: Annotated[str | None, Field(description="Or: the episode...")] = None,
    target: Annotated[str | None, Field(description="...and which export, e.g. 'youtube-1080p'.")] = None,
) -> dict[str, Any]:
    """Check a finished file against its platform's rules (format, size, loudness). Pass or fail per rule."""
    return engine.call("POST", "/api/check-export", {"file": file, "episode": episode, "target": target})


@server.tool(annotations=READ, structured_output=False)
def get_activity_log(
    limit: Annotated[int, Field(description="How many entries, newest first.", ge=1, le=200)] = 20,
    episode: Annotated[str | None, Field(description="Optional: only this episode.")] = None,
) -> dict[str, Any]:
    """Recent changes by Nick, Claude and Cutzo itself, newest first."""
    res = engine.call("GET", "/api/activity", query={"limit": limit, "episode": episode, "reads": "false"})
    return {"entries": [{"when": e["at"], "who": e["who"], "what": e["summary"], "ok": e["ok"]} for e in res["entries"]]}


@server.tool(annotations=READ, structured_output=False)
def show_in_cutzo(episode: Annotated[str | None, Field(description="Optional episode to open.")] = None) -> dict[str, Any]:
    """Open the Cutzo window in Nick's browser (on the episode, if given) so he can see the result."""
    path = "/"
    if episode:
        e = engine.call("GET", _ep(episode))
        path = f"/#/episode/{urllib.parse.quote(e['id'])}"
    else:
        engine.ensure_running()
    url = engine.base + path
    opened = webbrowser.open(url)
    return {"opened": bool(opened), "url": url}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
