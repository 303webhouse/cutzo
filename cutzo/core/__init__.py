"""Cutzo's core library. Everything the app and Claude can do lives on `Cutzo`."""

from typing import TYPE_CHECKING, Any

from .errors import CutzoError, NeedsConfirmation, NotAllowed, NotFound

if TYPE_CHECKING:
    from .service import Cutzo

__all__ = ["Cutzo", "CutzoError", "NeedsConfirmation", "NotAllowed", "NotFound"]


def __getattr__(name: str) -> Any:
    # `Cutzo` loads on first use, so small modules (errors, audio, ...) can be imported on their
    # own: cutzo.tools needs cutzo.core.errors, and service.py needs cutzo.tools.
    if name == "Cutzo":
        from .service import Cutzo

        return Cutzo
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
