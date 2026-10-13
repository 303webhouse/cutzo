"""Safety rules.

1. Cutzo never changes or deletes original recordings. It only reads them.
2. Cutzo writes only inside its own folders in the library (see WRITABLE).
3. Replacing a finished export needs an explicit confirm (handled in service.py).
4. Every change is written to the activity log (handled in service.py).
"""

from __future__ import annotations

from pathlib import Path

from .errors import NotAllowed

WRITABLE = ("episodes", "exports", "kit", "jobs", "logs", "samples", "shows", "trash")
WRITABLE_FILES = ("activity.jsonl",)


class Guard:
    def __init__(self, library: Path):
        self.library = Path(library).resolve()
        self.roots = [(self.library / name).resolve() for name in WRITABLE]
        self.files = [(self.library / name).resolve() for name in WRITABLE_FILES]

    def is_writable(self, path: Path | str) -> bool:
        target = Path(path).resolve()
        if target in self.files:
            return True
        return any(target == root or root in target.parents for root in self.roots)

    def writable(self, path: Path | str) -> Path:
        """Return the resolved path, or refuse if it is outside Cutzo's own folders."""
        target = Path(path).resolve()
        if not self.is_writable(target):
            raise NotAllowed(
                f"Blocked: Cutzo only writes inside its own folders in {self.library}, not {target}.",
                hint="Original recordings and other folders are read-only to Cutzo.",
            )
        return target

    def is_original(self, path: Path | str) -> bool:
        """Anything outside the library is someone's original file."""
        target = Path(path).resolve()
        return not (target == self.library or self.library in target.parents)
