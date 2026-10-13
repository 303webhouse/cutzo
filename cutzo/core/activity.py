"""The activity log: every change, who made it (you, Claude or Cutzo itself) and when.

Stored as one JSON object per line in activity.jsonl in the library folder.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import append_line

SOURCES = {"you": "You", "claude": "Claude", "cutzo": "Cutzo"}
MAX_BYTES = 5_000_000


class ActivityLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._entries: list[dict[str, Any]] = []
        self._next_id = 1
        self._load()

    def add(self, *, source: str, action: str, summary: str, episode: str | None = None,
            ok: bool = True, details: dict[str, Any] | None = None) -> dict[str, Any]:
        source = source if source in SOURCES else "cutzo"
        with self._lock:
            entry = {
                "id": self._next_id,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": source,
                "who": SOURCES[source],
                "action": action,
                "summary": summary,
                "episode": episode,
                "ok": ok,
            }
            if details:
                entry["details"] = details
            self._next_id += 1
            self._entries.append(entry)
            self._rotate_if_big()
            append_line(self.path, json.dumps(entry, ensure_ascii=False))
            if len(self._entries) > 2000:
                self._entries = self._entries[-1000:]
            return dict(entry)

    def recent(self, *, limit: int = 50, since: int | None = None, episode: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = self._entries
            if since is not None:
                items = [e for e in items if e["id"] > since]
            if episode:
                items = [e for e in items if e.get("episode") == episode]
            return [dict(e) for e in reversed(items[-max(1, limit):])]

    @property
    def last_id(self) -> int:
        with self._lock:
            return self._next_id - 1

    def last_from(self, source: str) -> dict[str, Any] | None:
        with self._lock:
            for e in reversed(self._entries):
                if e["source"] == source:
                    return dict(e)
        return None

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines[-1000:]:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and isinstance(entry.get("id"), int):
                self._entries.append(entry)
        if self._entries:
            self._next_id = max(e["id"] for e in self._entries) + 1

    def _rotate_if_big(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                old = self.path.parent / "logs" / f"activity-{stamp}.jsonl"
                old.parent.mkdir(parents=True, exist_ok=True)
                self.path.rename(old)
        except OSError:
            pass
