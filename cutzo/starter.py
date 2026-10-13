"""Starting Cutzo's engine so it keeps running on its own.

Whoever starts the engine (the desktop shortcut or the Claude connector) may close
soon after. On Windows the Claude connector runs inside a "job" that Windows ends,
together with everything started from it, when Claude stops the connector. So on
Windows the engine is started through a Task Scheduler task ("Cutzo Engine", made by
the setup script), which runs it outside that job. If the task is missing, it falls
back to starting the engine directly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .config import APP_DIR

TASK_NAME = "Cutzo Engine"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


def start_engine(library: Path) -> subprocess.Popen | None:
    """Start the engine in the background. Returns the process when this call owns it,
    or None when Windows' Task Scheduler started it (then only the health check can tell)."""
    if os.name == "nt" and run_task():
        return None
    return popen_engine(library)


def run_task() -> bool:
    try:
        result = subprocess.run(["schtasks.exe", "/run", "/tn", TASK_NAME], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
                                creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def popen_engine(library: Path) -> subprocess.Popen:
    exe = Path(sys.executable)
    kwargs: dict = {}
    if os.name == "nt":
        windowless = exe.with_name("pythonw.exe")
        if windowless.exists():
            exe = windowless
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    else:
        kwargs["start_new_session"] = True
    logs = Path(library) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    cmd = [str(exe), "-m", "cutzo.engine"]
    with open(logs / "engine-start.log", "ab") as out:
        try:
            return subprocess.Popen(cmd, cwd=str(APP_DIR), stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                    close_fds=True, **kwargs)
        except OSError:
            if os.name != "nt":
                raise
            # Leaving the job isn't allowed here; start it normally (it may stop with its parent).
            kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            return subprocess.Popen(cmd, cwd=str(APP_DIR), stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                    close_fds=True, **kwargs)


def tail(path: Path, lines: int = 6, sep: str = "\n") -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return sep.join(text[-lines:])
