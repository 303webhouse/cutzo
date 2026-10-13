"""Add (or remove) Cutzo's connector in the Claude desktop app's settings.

The setup script runs this. By hand:
    python -m cutzo.connector_install            add or update the connector
    python -m cutzo.connector_install --remove   take it out again
    python -m cutzo.connector_install --check    show where it is set up

It edits claude_desktop_config.json, keeps everything else in that file exactly
as it was, and saves a backup copy next to it first. The Claude desktop app
reads the file when it starts, so quit and reopen it afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

SERVER_NAME = "cutzo"
KEEP_BACKUPS = 5


def config_paths() -> list[Path]:
    """Where the Claude desktop app may keep its settings file, most likely first."""
    paths: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    local = os.environ.get("LOCALAPPDATA")
    if local and (Path(local) / "Packages").is_dir():
        # The Microsoft Store / MSIX version can keep a private copy here.
        for pkg in sorted((Path(local) / "Packages").glob("Claude_*")):
            paths.append(pkg / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json")
    if sys.platform == "darwin":
        paths.append(Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    elif os.name != "nt":
        paths.append(Path.home() / ".config" / "Claude" / "claude_desktop_config.json")
    return paths


def server_entry() -> dict[str, Any]:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":  # the connector talks over stdin/stdout, so it needs python.exe
        exe = exe.with_name("python.exe")
    return {"command": str(exe), "args": ["-m", "cutzo.mcp_server"]}


def is_store_python() -> bool:
    """Microsoft Store Python silently redirects writes in AppData, so Claude would never see the change."""
    where = f"{getattr(sys, '_base_executable', '')} {sys.base_prefix}"
    return os.name == "nt" and "WindowsApps" in where


def _read(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("the file doesn't hold a settings object")
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    json.loads(text)  # never write something Claude can't read back
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cutzo-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _backup(path: Path, stamp: str) -> Path:
    backup = path.with_name(f"claude_desktop_config.cutzo-backup-{stamp}.json")
    shutil.copy2(path, backup)
    old = sorted(path.parent.glob("claude_desktop_config.cutzo-backup-*.json"))
    for extra in old[:-KEEP_BACKUPS]:
        try:
            extra.unlink()
        except OSError:
            pass
    return backup


def install(paths: list[Path] | None = None, *, remove: bool = False, stamp: str | None = None) -> list[str]:
    if is_store_python():
        raise SystemExit("This Python is the Microsoft Store version, which can't change Claude's settings. "
                         "Run Setup Cutzo again; it uses its own Python.")
    paths = list(paths if paths is not None else config_paths())
    if not paths:
        raise SystemExit("Couldn't work out where the Claude desktop app keeps its settings.")
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    targets = [p for p in paths if p.exists()] or paths[:1]
    messages = []
    for path in targets:
        try:
            data = _read(path) if path.exists() else {}
        except (OSError, ValueError) as exc:
            raise SystemExit(f"Claude's settings file {path} couldn't be read ({exc}). "
                             "Nothing was changed. Ask Claude to look at it with you.") from None
        servers = data.get("mcpServers")
        if servers is None:
            servers = data["mcpServers"] = {}
        if not isinstance(servers, dict):
            raise SystemExit(f"Claude's settings file {path} has an unexpected shape. Nothing was changed.")
        others = sorted(k for k in servers if k != SERVER_NAME)
        if remove:
            if SERVER_NAME not in servers:
                messages.append(f"Cutzo wasn't set up in {path}; nothing to remove.")
                continue
            backup = _backup(path, stamp)
            del servers[SERVER_NAME]
        else:
            if servers.get(SERVER_NAME) == server_entry():
                messages.append(f"Cutzo's connector is already set up in {path}.")
                continue
            backup = _backup(path, stamp) if path.exists() else None
            servers[SERVER_NAME] = server_entry()
        _write(path, data)
        check = _read(path).get("mcpServers", {})
        missing = [k for k in others if k not in check]
        if missing:  # pragma: no cover - would mean a bug; put the original back
            if backup:
                shutil.copy2(backup, path)
            raise SystemExit(f"Something went wrong; your original settings were put back from {backup}.")
        action = "Removed Cutzo's connector from" if remove else "Added Cutzo's connector to"
        kept = f" Your other connectors are untouched: {', '.join(others)}." if others else ""
        saved = f" Backup: {backup.name}." if backup else ""
        messages.append(f"{action} {path}.{kept}{saved}")
    return messages


def check(paths: list[Path] | None = None) -> list[str]:
    out = []
    for path in paths if paths is not None else config_paths():
        if not path.exists():
            out.append(f"{path}: not there")
            continue
        try:
            entry = _read(path).get("mcpServers", {}).get(SERVER_NAME)
        except (OSError, ValueError) as exc:
            out.append(f"{path}: couldn't read it ({exc})")
            continue
        out.append(f"{path}: {'set up -> ' + entry['command'] if entry else 'Cutzo not set up'}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Connect Cutzo to the Claude desktop app.")
    parser.add_argument("--remove", action="store_true", help="Take Cutzo's connector out again.")
    parser.add_argument("--check", action="store_true", help="Only show where it is set up.")
    args = parser.parse_args(argv)
    if args.check:
        print("\n".join(check()))
        return 0
    for line in install(remove=args.remove):
        print(f"   {line}")
    if not args.remove:
        print("   Quit the Claude desktop app (right-click its icon by the clock, then Quit) and open it again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
