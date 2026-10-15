"""Download Cutzo's outside programs and check them. The setup script runs:

    python -m cutzo.tools.install            install whatever is missing
    python -m cutzo.tools.install --check    only report what is there

Every download is checked against the SHA-256 recorded in cutzo/tools/__init__.py
before it is used, then unpacked into <app>/tools/<name>-<version>/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import TOOLS, Tool, installed_path, platform_key, tools_dir

MARKER = ".cutzo-tool.json"
CHUNK = 1 << 20


def is_installed(tool: Tool) -> bool:
    return all(installed_path(p).is_file() for p in tool.programs)


def _download(url: str, dest: Path, expected_size: int, report: Callable[[str], None]) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Cutzo setup"})
    digest = hashlib.sha256()
    done = 0
    shown = -1
    with urllib.request.urlopen(request, timeout=60) as response, open(dest, "wb") as out:
        total = int(response.headers.get("Content-Length") or expected_size or 0)
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if total:
                tenth = done * 10 // total
                if tenth != shown:
                    shown = tenth
                    report(f"{done / 1e6:.0f} of {total / 1e6:.0f} MB")
    return digest.hexdigest()


def _unzip(archive: Path, inner: str | None, dest: Path) -> None:
    prefix = (inner.strip("/") + "/") if inner else ""
    root = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if prefix and not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if not rel or rel.endswith("/"):
                continue
            target = (dest / rel).resolve()
            if root not in target.parents:
                raise RuntimeError(f"The download contains an unsafe path ({name}); not using it.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, CHUNK)


def _make_runnable(path: Path) -> None:
    if os.name != "nt":
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install(tool: Tool, report: Callable[[str], None] = print) -> Path:
    """Download, check and unpack one tool. Returns its folder."""
    download = tool.downloads.get(platform_key())
    if download is None:
        raise RuntimeError(f"there's no {tool.label} download for this kind of computer ({platform_key()}); "
                           f"install {tool.label} yourself so it's on the PATH")
    root = tools_dir()
    root.mkdir(parents=True, exist_ok=True)
    final = root / tool.folder
    partial = root / f"{tool.folder}.partial"
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir()
    try:
        with tempfile.TemporaryDirectory(dir=root, prefix="download-") as tmp:
            file = Path(tmp) / download.url.rsplit("/", 1)[-1]
            digest = _download(download.url, file, download.size, report)
            if digest != download.sha256:
                raise RuntimeError("the download didn't match its checksum, so Cutzo didn't use it; try again later")
            if download.kind == "zip":
                _unzip(file, download.inner, partial)
            else:
                (rel,) = tool.programs.values()
                target = partial / (rel + (".exe" if os.name == "nt" else ""))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, target)
        for rel in tool.programs.values():
            program = partial / (rel + (".exe" if os.name == "nt" else ""))
            if not program.is_file():
                raise RuntimeError(f"the download didn't contain {program.name}")
            _make_runnable(program)
        _write_marker(partial, tool, download.sha256)
        if final.exists():
            shutil.rmtree(final)
        partial.rename(final)
    finally:
        shutil.rmtree(partial, ignore_errors=True)
    return final


def _write_marker(folder: Path, tool: Tool, sha256: str | None) -> None:
    (folder / MARKER).write_text(json.dumps({
        "tool": tool.name,
        "version": tool.version,
        "sha256": sha256,
        "installed": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")


def remove_old_versions(tool: Tool) -> list[str]:
    """Delete other versions of a tool that Cutzo itself installed (they carry the marker file)."""
    removed = []
    root = tools_dir()
    if not root.is_dir():
        return removed
    for folder in root.glob(f"{tool.name}-*"):
        if folder.name == tool.folder or not (folder / MARKER).is_file():
            continue
        try:
            data = json.loads((folder / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("tool") == tool.name:
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder.name)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cutzo.tools.install", description=__doc__)
    parser.add_argument("--check", action="store_true", help="only report, don't download")
    parser.add_argument("--only", choices=sorted(TOOLS), help="just this tool")
    args = parser.parse_args(argv)
    problems = 0
    for tool in TOOLS.values():
        if args.only and tool.name != args.only:
            continue
        if is_installed(tool):
            folder = tools_dir() / tool.folder
            if not (folder / MARKER).is_file():
                _write_marker(folder, tool, None)
            print(f"   {tool.label} {tool.version} is ready.", flush=True)
            continue
        if args.check:
            print(f"   {tool.label} {tool.version} isn't installed yet.", flush=True)
            problems += 1
            continue
        print(f"   Downloading {tool.label} {tool.version}...", flush=True)
        try:
            install(tool, report=lambda text: print(f"      {text}", flush=True))
        except Exception as exc:  # report every failure in plain words, keep going
            print(f"   Couldn't install {tool.label}: {exc}.", flush=True)
            problems += 1
            continue
        old = remove_old_versions(tool)
        print(f"   {tool.label} {tool.version} is ready." + (f" Removed {', '.join(old)}." if old else ""), flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
