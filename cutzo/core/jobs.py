"""Background jobs (import, build, export...) with progress the app and Claude can poll.

Each job is saved as jobs/<id>.json so its status survives a restart. A job that
was still running when Cutzo closed is marked "interrupted" on the next start.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .errors import CutzoError, NotFound
from .storage import read_json, write_json

log = logging.getLogger("cutzo.jobs")

ACTIVE = ("queued", "running")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobCancelled(Exception):
    pass


class JobContext:
    """Handed to the work function so it can report progress."""

    def __init__(self, runner: "JobRunner", job_id: str, speed: float):
        self._runner = runner
        self.job_id = job_id
        self._speed = speed

    def step(self, label: str, progress: float, detail: str | None = None) -> None:
        self.check()
        self._runner._update(self.job_id, step=label, progress=round(max(0.0, min(1.0, progress)), 3),
                             detail=detail)

    def sleep(self, seconds: float) -> None:
        """Pretend work (Phase 1). Wakes up early if the job is cancelled."""
        end = time.monotonic() + seconds * self._speed
        while True:
            self.check()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.1, left))

    def check(self) -> None:
        if self._runner._is_cancelled(self.job_id):
            raise JobCancelled()


class JobRunner:
    def __init__(self, folder: Path, *, workers: int = 2, speed: float = 1.0,
                 on_finish: Callable[[dict[str, Any]], None] | None = None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.speed = speed
        self.on_finish = on_finish
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._cancel: set[str] = set()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cutzo-job")
        self._load()

    # ------------------------------------------------------------------ public
    def submit(self, *, kind: str, title: str, work: Callable[[JobContext], dict[str, Any] | None],
               episode: str | None = None, source: str = "you", steps: list[str] | None = None) -> dict[str, Any]:
        job_id = f"job-{datetime.now().strftime('%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        job = {
            "id": job_id,
            "kind": kind,
            "title": title,
            "episode": episode,
            "source": source,
            "status": "queued",
            "progress": 0.0,
            "step": "Waiting to start",
            "detail": None,
            "steps": steps or [],
            "created": now_iso(),
            "started": None,
            "finished": None,
            "result": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._save(job)
        self._pool.submit(self._run, job_id, work)
        return dict(job)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise NotFound(f"No job called {job_id!r}.", hint="Job ids look like job-1013-142501-ab12.")
            return dict(job)

    def list(self, *, limit: int = 30, active_only: bool = False, episode: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["created"], reverse=True)
            if active_only:
                jobs = [j for j in jobs if j["status"] in ACTIVE]
            if episode:
                jobs = [j for j in jobs if j.get("episode") == episode]
            return [dict(j) for j in jobs[:limit]]

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] not in ACTIVE:
            return job
        with self._lock:
            self._cancel.add(job_id)
        return self.get(job_id)

    def active_for(self, episode: str, kind: str | None = None) -> list[dict[str, Any]]:
        return [j for j in self.list(limit=200, active_only=True, episode=episode) if kind in (None, j["kind"])]

    def wait(self, job_id: str, timeout: float = 30.0) -> dict[str, Any]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            job = self.get(job_id)
            if job["status"] not in ACTIVE:
                return job
            time.sleep(0.02)
        return self.get(job_id)

    def shutdown(self) -> None:
        with self._lock:
            self._cancel.update(j for j, job in self._jobs.items() if job["status"] in ACTIVE)
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ----------------------------------------------------------------- private
    def _run(self, job_id: str, work: Callable[[JobContext], dict[str, Any] | None]) -> None:
        ctx = JobContext(self, job_id, self.speed)
        self._update(job_id, status="running", started=now_iso(), step="Starting")
        try:
            result = work(ctx)
        except JobCancelled:
            self._update(job_id, status="cancelled", finished=now_iso(), step="Cancelled")
        except CutzoError as exc:
            self._update(job_id, status="failed", finished=now_iso(), error=exc.message, step="Stopped")
        except Exception as exc:  # pragma: no cover - logged for debugging
            log.error("Job %s crashed:\n%s", job_id, traceback.format_exc())
            self._update(job_id, status="failed", finished=now_iso(), step="Stopped",
                         error=f"Unexpected problem: {exc}. Details are in Cutzo's log.")
        else:
            self._update(job_id, status="done", finished=now_iso(), progress=1.0, step="Done", result=result or {})
        finally:
            with self._lock:
                self._cancel.discard(job_id)
        if self.on_finish:
            try:
                self.on_finish(self.get(job_id))
            except Exception:  # pragma: no cover
                log.error("on_finish failed:\n%s", traceback.format_exc())

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update({k: v for k, v in changes.items() if v is not None or k in ("detail",)})
            self._save(job)

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancel

    def _save(self, job: dict[str, Any]) -> None:
        write_json(self.folder / f"{job['id']}.json", job)

    def _load(self) -> None:
        files = sorted(self.folder.glob("job-*.json"))
        for path in files[-300:]:
            job = read_json(path)
            if not isinstance(job, dict) or "id" not in job:
                continue
            if job.get("status") in ACTIVE:
                job.update(status="interrupted", step="Interrupted",
                           error="Cutzo was closed while this was running. Start it again to redo it.",
                           finished=job.get("finished") or now_iso())
                self._save(job)
            self._jobs[job["id"]] = job
