"""What the Cutzo desktop shortcut runs.

1. If Cutzo's engine is already running, just open the browser.
2. Otherwise start the engine in the background (no black window), wait until
   it answers, then open the browser at http://127.0.0.1:8765.
3. If anything goes wrong, show a plain-English message box.
"""

from __future__ import annotations

import json
import os
import sys
import time
import webbrowser

from .config import LOCAL, SettingsError, load_settings
from .starter import start_engine, tail


def _health(base_url: str, timeout: float = 1.0) -> dict | None:
    try:
        with LOCAL.open(f"{base_url}/api/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data if data.get("app") == "cutzo" else None
    except Exception:
        return None


def _message(title: str, text: str) -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)  # information icon
            return
        except Exception:
            pass
    if sys.stderr:
        print(f"{title}: {text}", file=sys.stderr)


def main() -> int:
    try:
        settings = load_settings()
    except SettingsError as exc:
        _message("Cutzo couldn't start", str(exc))
        return 1
    base = settings.base_url
    if not _health(base):
        logs = settings.library / "logs"
        try:
            proc = start_engine(settings.library)
        except Exception as exc:  # pragma: no cover - shown to the user
            _message("Cutzo couldn't start", f"Cutzo's engine didn't start:\n{exc}\n\nTry running Setup Cutzo again.")
            return 1
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if _health(base):
                break
            if proc is not None and proc.poll() is not None:
                if _health(base, timeout=3):  # another copy started at the same moment
                    break
                details = tail(logs / "engine-start.log") or tail(logs / "engine.log")
                _message("Cutzo couldn't start",
                         "Cutzo's engine stopped right after starting.\n\n"
                         f"If another program uses port {settings.port}, change \"port\" in settings.json.\n\n"
                         f"Last lines of the log ({logs}):\n{details}")
                return 1
            time.sleep(0.4)
        else:
            details = tail(logs / "engine.log") or tail(logs / "engine-start.log")
            _message("Cutzo is slow to start", "Cutzo hasn't answered yet. Try the shortcut again in a minute.\n\n"
                                               f"Last lines of the log ({logs}):\n{details}")
            return 1
    webbrowser.open(base + "/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
