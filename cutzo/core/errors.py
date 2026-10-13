"""Errors Cutzo can explain in plain words.

Every error carries a message for the person (shown in the app and to Claude)
and an optional hint about what to do next.
"""

from __future__ import annotations

from typing import Any


class CutzoError(Exception):
    status = 400

    def __init__(self, message: str, *, hint: str | None = None, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.data = data or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.data:
            out["data"] = self.data
        return out


class NotFound(CutzoError):
    status = 404


class NotAllowed(CutzoError):
    """Blocked by a safety rule (for example, writing outside Cutzo's folders)."""

    status = 403


class NeedsConfirmation(CutzoError):
    """The action would replace something finished; repeat it with confirm=true."""

    status = 409
