"""Cutzo's engine: a small web server on this computer only (http://127.0.0.1:8765).

It serves the browser screens and the JSON routes the screens and the Claude
connector use. Every route is a thin wrapper over a `Cutzo` method.

Run it with:  python -m cutzo.engine
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import socket
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import urlparse

from . import __version__
from .config import LOCAL, WEB_DIR, Settings, SettingsError, load_settings

# Windows sometimes maps .js to text/plain in the registry, which breaks the
# browser screens. Set the types explicitly before anything serves files.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("audio/wav", ".wav")
mimetypes.add_type("image/x-icon", ".ico")

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from .core import Cutzo, CutzoError  # noqa: E402
from .core import presets as presetlib  # noqa: E402

log = logging.getLogger("cutzo.engine")

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SOURCES = {"app": "you", "you": "you", "claude": "claude"}
TimeValue = Union[str, float]


# ---------------------------------------------------------------- bodies
class CreateBody(BaseModel):
    show: str | None = None
    title: str | None = None
    folder: str | None = None


class ImportBody(BaseModel):
    paths: list[str] | str


class BuildBody(BaseModel):
    template: str | None = None


class CutBody(BaseModel):
    start: TimeValue
    end: TimeValue
    reason: str = "manual"
    confirm: bool = False


class CutWordsBody(BaseModel):
    text: str
    occurrence: Union[int, str, None] = None
    confirm: bool = False


class CameraBody(BaseModel):
    start: TimeValue
    end: TimeValue
    angle: str


class ChapterBody(BaseModel):
    time: TimeValue
    title: str


class AssetBody(BaseModel):
    slot: str
    asset: str


class ExportBody(BaseModel):
    targets: list[str] | str | None = None
    confirm: bool = False


class CheckBody(BaseModel):
    file: str | None = None
    episode: str | None = None
    target: str | None = None


class DeadAirBody(BaseModel):
    longer_than: float | None = None
    keep: float | None = None


class VoiceBody(BaseModel):
    strength: float


class PickBody(BaseModel):
    kind: str
    start: TimeValue
    end: TimeValue


class RevealBody(BaseModel):
    file: str


# ------------------------------------------------------------------- app
def _host_of(request: Request) -> str:
    raw = (request.headers.get("host") or "").strip().lower()
    if raw.startswith("["):  # [::1]:8765
        return raw[1:].split("]", 1)[0]
    return raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw


def source_of(request: Request) -> str:
    return SOURCES.get((request.headers.get("x-cutzo-source") or "app").lower(), "you")


def create_app(cutzo: Cutzo, *, extra_hosts: tuple[str, ...] = ()) -> FastAPI:
    """extra_hosts is for tests only (the test client calls itself "testserver")."""
    app = FastAPI(title="Cutzo", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cutzo = cutzo
    app.state.server = None
    local_hosts = LOCAL_HOSTS | set(extra_hosts)

    @app.middleware("http")
    async def only_this_computer(request: Request, call_next):
        # Refuse anything that isn't from this PC (blocks DNS-rebinding tricks), and
        # require Cutzo's own header on changes so other websites can't send them.
        if _host_of(request) not in local_hosts:
            return JSONResponse({"error": "Cutzo only answers requests from this computer."}, status_code=403)
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            if origin and (urlparse(origin).hostname or "").lower() not in local_hosts:
                return JSONResponse({"error": "Blocked a request from another website."}, status_code=403)
            if (request.headers.get("x-cutzo-source") or "").lower() not in SOURCES:
                return JSONResponse({"error": "Missing the X-Cutzo-Source header."}, status_code=403)
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache"
        # Other websites may not show Cutzo inside a frame (no clickjacking).
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(CutzoError)
    async def on_cutzo_error(request: Request, exc: CutzoError):
        if request.method not in ("GET", "HEAD"):
            action = request.url.path.rstrip("/").rsplit("/", 1)[-1] or "action"
            cutzo.log_failure(source_of(request), action, exc.message, exc.status)
        return JSONResponse(exc.to_dict(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def on_bad_request(request: Request, exc: RequestValidationError):
        problems = []
        for err in exc.errors():
            where = ".".join(str(p) for p in err.get("loc", []) if p != "body")
            problems.append(f"{where}: {err.get('msg')}" if where else str(err.get("msg")))
        return JSONResponse({"error": "Some details were missing or wrong: " + "; ".join(problems)}, status_code=422)

    @app.exception_handler(Exception)
    async def on_crash(request: Request, exc: Exception):
        log.error("Crash on %s %s:\n%s", request.method, request.url.path, traceback.format_exc())
        return JSONResponse({"error": f"Something went wrong inside Cutzo: {exc}. Details are in Cutzo's log."},
                            status_code=500)

    # ------------------------------------------------------------ status
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return cutzo.info()

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        """Cheap check the screens poll to notice changes (for example, from Claude)."""
        return {"activity_last_id": cutzo.activity.last_id,
                "active_jobs": cutzo.list_jobs(active_only=True, limit=20)}

    # ------------------------------------------------------- shows, kit
    @app.get("/api/shows")
    def shows(request: Request):
        return cutzo.list_shows(source=source_of(request))

    @app.get("/api/shows/{show}")
    def show(show: str, request: Request):
        return cutzo.get_show(show, source=source_of(request))

    @app.get("/api/assets")
    def assets(request: Request, show: str | None = None):
        return cutzo.list_assets(show, source=source_of(request))

    @app.get("/api/presets")
    def presets():
        return presetlib.list_presets(include_reserve=True)

    # --------------------------------------------------------- episodes
    @app.get("/api/episodes")
    def episodes(request: Request, show: str | None = None):
        return cutzo.list_episodes(show, source=source_of(request))

    @app.post("/api/episodes")
    def create_episode(body: CreateBody, request: Request):
        return cutzo.create_episode(body.show, body.title, body.folder, source=source_of(request))

    @app.get("/api/episodes/{episode}")
    def episode(episode: str, request: Request):
        return cutzo.get_episode(episode, source=source_of(request))

    @app.delete("/api/episodes/{episode}")
    def remove_episode(episode: str, request: Request):
        return cutzo.remove_episode(episode, source=source_of(request))

    @app.post("/api/episodes/{episode}/import")
    def import_media(episode: str, body: ImportBody, request: Request):
        return cutzo.import_media(episode, body.paths, source=source_of(request))

    @app.post("/api/episodes/{episode}/build")
    def build(episode: str, body: BuildBody, request: Request):
        return cutzo.build_episode(episode, body.template, source=source_of(request))

    @app.get("/api/episodes/{episode}/transcript")
    def transcript(episode: str, request: Request, include_cut: bool = False, format: str = "text",
                   start: Optional[str] = None, end: Optional[str] = None):
        return cutzo.get_transcript(episode, include_cut=include_cut, fmt=format, source=source_of(request),
                                    start=start, end=end)

    @app.post("/api/episodes/{episode}/transcribe")
    def transcribe(episode: str, request: Request):
        return cutzo.transcribe(episode, source=source_of(request))

    @app.get("/api/episodes/{episode}/waveform")
    def waveform(episode: str, buckets: int = 1200, start: float | None = None, end: float | None = None):
        return cutzo.waveform(episode, buckets, start=start, end=end)

    @app.get("/api/episodes/{episode}/audio")
    def audio(episode: str, which: str = "cleaned"):
        path = cutzo.preview_audio(episode, which=which)
        return FileResponse(path, media_type="audio/mp4" if path.suffix == ".m4a" else "audio/wav")

    @app.post("/api/episodes/{episode}/dead-air")
    def dead_air(episode: str, body: DeadAirBody, request: Request):
        return cutzo.set_dead_air(episode, body.longer_than, body.keep, source=source_of(request))

    @app.post("/api/episodes/{episode}/voice")
    def voice(episode: str, body: VoiceBody, request: Request):
        return cutzo.set_voice(episode, body.strength, source=source_of(request))

    @app.post("/api/episodes/{episode}/picks")
    def add_pick(episode: str, body: PickBody, request: Request):
        return cutzo.add_pick(episode, body.kind, body.start, body.end, source=source_of(request))

    @app.delete("/api/episodes/{episode}/picks/{pick_id}")
    def remove_pick(episode: str, pick_id: str, request: Request):
        return cutzo.remove_pick(episode, pick_id, source=source_of(request))

    @app.post("/api/episodes/{episode}/cuts")
    def cut_section(episode: str, body: CutBody, request: Request):
        return cutzo.cut_section(episode, body.start, body.end, source=source_of(request), reason=body.reason,
                                 confirm=body.confirm)

    @app.post("/api/episodes/{episode}/cut-words")
    def cut_words(episode: str, body: CutWordsBody, request: Request):
        return cutzo.cut_words(episode, body.text, body.occurrence, source=source_of(request), confirm=body.confirm)

    @app.delete("/api/episodes/{episode}/edits/{edit_id}")
    def restore(episode: str, edit_id: str, request: Request):
        return cutzo.restore_cut(episode, edit_id, source=source_of(request))

    @app.post("/api/episodes/{episode}/camera")
    def camera(episode: str, body: CameraBody, request: Request):
        return cutzo.set_camera(episode, body.start, body.end, body.angle, source=source_of(request))

    @app.post("/api/episodes/{episode}/chapters")
    def add_chapter(episode: str, body: ChapterBody, request: Request):
        return cutzo.add_chapter(episode, body.time, body.title, source=source_of(request))

    @app.delete("/api/episodes/{episode}/chapters/{chapter_id}")
    def remove_chapter(episode: str, chapter_id: str, request: Request):
        return cutzo.remove_chapter(episode, chapter_id, source=source_of(request))

    @app.post("/api/episodes/{episode}/assets")
    def swap_asset(episode: str, body: AssetBody, request: Request):
        return cutzo.swap_asset(episode, body.slot, body.asset, source=source_of(request))

    @app.post("/api/episodes/{episode}/undo")
    def undo(episode: str, request: Request):
        return cutzo.undo(episode, source=source_of(request))

    @app.post("/api/episodes/{episode}/export")
    def export(episode: str, body: ExportBody, request: Request):
        return cutzo.export(episode, body.targets, confirm=body.confirm, source=source_of(request))

    @app.post("/api/check-export")
    def check_export(body: CheckBody, request: Request):
        return cutzo.check_export(body.file, body.episode, body.target, source=source_of(request))

    @app.post("/api/reveal")
    def reveal(body: RevealBody):
        return cutzo.reveal(body.file)

    # ------------------------------------------------------------- jobs
    @app.get("/api/jobs")
    def jobs(active: bool = False, limit: int = 30):
        return cutzo.list_jobs(active_only=active, limit=limit)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str, request: Request):
        return cutzo.job_status(job_id, source=source_of(request))

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str, request: Request):
        return cutzo.cancel_job(job_id, source=source_of(request))

    # -------------------------------------------------------- activity
    @app.get("/api/activity")
    def activity(limit: int = 50, since: int | None = None, episode: str | None = None, reads: bool = True):
        return cutzo.activity_log(limit=limit, since=since, episode=episode, include_reads=reads)

    # ------------------------------------------------------ maintenance
    @app.get("/api/scan")
    def scan(folder: str):
        return cutzo.scan(folder)

    @app.post("/api/self-test")
    def self_test(request: Request):
        return cutzo.self_test(source=source_of(request))

    @app.post("/api/samples/reset")
    def reset_samples(request: Request):
        return cutzo.reset_samples(source=source_of(request))

    @app.post("/api/shutdown")
    def shutdown(request: Request):
        cutzo._log(source_of(request), "shutdown", "Closed Cutzo")
        server = app.state.server
        if server is not None:
            threading.Timer(0.4, lambda: setattr(server, "should_exit", True)).start()
        return {"ok": True, "message": "Cutzo is closing. Open it again from the desktop shortcut."}

    # ---------------------------------------------------------- screens
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html", media_type="text/html")

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(WEB_DIR / "cutzo.ico", media_type="image/x-icon")

    return app


# ------------------------------------------------------------------ main
def _quiet_streams() -> None:
    # Started without a console (pythonw), print() and logging would crash.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def setup_logging(library: Path) -> Path:
    logs = library / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "engine.log"
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(console)
    return path


def running_cutzo(settings: Settings, timeout: float = 1.5) -> dict[str, Any] | None:
    """The health answer of a Cutzo engine already running on this port, if any."""
    try:
        with LOCAL.open(f"{settings.base_url}/api/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data if data.get("app") == "cutzo" else None
    except Exception:
        return None


def claim_port(host: str, port: int) -> socket.socket:
    """Take the port before anything else, so two engines can never run on one library."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", 0xFFFFFFFB), 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    sock.set_inheritable(False)
    return sock


def main(argv: list[str] | None = None) -> int:
    _quiet_streams()
    parser = argparse.ArgumentParser(description="Run Cutzo's engine.")
    parser.add_argument("--port", type=int, help="Port to use (default from settings, 8765).")
    parser.add_argument("--library", help="Library folder (default from settings, F:\\Cutzo).")
    parser.add_argument("--speed", type=float, default=float(os.environ.get("CUTZO_SPEED") or 1.0),
                        help="Phase 1 pretend-work speed (0.1 = 10x faster).")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"Cutzo can't start: {exc}", file=sys.stderr)
        return 2
    if args.port or args.library:
        settings = Settings(library=Path(args.library) if args.library else settings.library,
                            port=args.port or settings.port, drive=settings.drive)
    log_path = setup_logging(settings.library)

    already = running_cutzo(settings)
    if already:
        log.info("Cutzo is already running (pid %s) at %s", already.get("pid"), settings.base_url)
        return 0
    try:
        sock = claim_port(settings.host, settings.port)
    except OSError:
        if running_cutzo(settings, timeout=5):
            log.info("Another copy of Cutzo started at the same moment; leaving it running.")
            return 0
        log.error("Port %s is used by another program. Change \"port\" in settings.json.", settings.port)
        return 2

    log.info("Starting Cutzo %s with library %s on %s", __version__, settings.library, settings.base_url)
    cutzo = Cutzo(settings.library, speed=args.speed, drive=settings.drive)
    app = create_app(cutzo)
    # A browser tab playing audio keeps a stream open; don't let that hold up quitting.
    config = uvicorn.Config(app, host=settings.host, port=settings.port, log_config=None, access_log=False,
                            log_level="warning", timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)
    app.state.server = server
    try:
        server.run(sockets=[sock])
    finally:
        cutzo.jobs.shutdown()
        log.info("Cutzo stopped. Log: %s", log_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
