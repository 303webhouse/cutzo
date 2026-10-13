"""What the Cutzo desktop shortcut runs.

1. If Cutzo's engine is already running, just open the browser.
2. Otherwise start the engine in the background (no black window), wait until
   it answers, then open the browser at http://127.0.0.1:8765.
3. If anything goes wrong, show a plain-English message box.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from .config import APP_DIR, LOCAL, SettingsError, load_settings

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


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


def _tail(path: Path, lines: int = 6) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return "\n".join(text[-lines:])


def _start_engine(library: Path) -> subprocess.Popen:
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    if os.name == "nt" and windowless.exists():
        exe = windowless
    logs = library / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    out = open(logs / "engine-start.log", "ab")
    kwargs = {"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    try:
        return subprocess.Popen([str(exe), "-m", "cutzo.engine"], cwd=str(APP_DIR), stdin=subprocess.DEVNULL,
                                stdout=out, stderr=out, close_fds=True, **kwargs)
    finally:
        out.close()


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
            proc = _start_engine(settings.library)
        except Exception as exc:  # pragma: no cover - shown to the user
            _message("Cutzo couldn't start", f"Cutzo's engine didn't start:\n{exc}\n\nTry running Setup Cutzo again.")
            return 1
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if _health(base):
                break
            if proc.poll() is not None:
                if _health(base, timeout=3):  # another copy started at the same moment
                    break
                details = _tail(logs / "engine-start.log") or _tail(logs / "engine.log")
                _message("Cutzo couldn't start",
                         "Cutzo's engine stopped right after starting.\n\n"
                         f"If another program uses port {settings.port}, change \"port\" in settings.json.\n\n"
                         f"Last lines of the log ({logs}):\n{details}")
                return 1
            time.sleep(0.4)
        else:
            _message("Cutzo is slow to start", "Cutzo hasn't answered yet. Try the shortcut again in a minute.\n"
                                               f"Details: {logs / 'engine.log'}")
            return 1
    webbrowser.open(base + "/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
