"""The outside programs Cutzo runs, each pinned to one version.

    FFmpeg        decoding, filters, loudness, encoding      (cutzo.tools.ffmpeg)
    DeepFilterNet noise removal for voices                   (cutzo.tools.deepfilter)

Setup downloads them into <app>/tools/<name>-<version>/ and checks each download
against the SHA-256 recorded here (see install.py). Anywhere else (tests, Linux)
Cutzo falls back to the same program on PATH. One module per program, so each can
be swapped without touching the rest of Cutzo.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..config import APP_DIR
from ..core.errors import CutzoError

TOOLS_DIR = APP_DIR / "tools"


@dataclass(frozen=True)
class Download:
    url: str
    sha256: str
    size: int
    kind: str  # "zip" (keep the folder `inner`) or "exe" (a single program)
    inner: str | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    label: str
    version: str
    programs: dict[str, str]  # program name -> path inside the tool's folder (no .exe)
    downloads: dict[str, Download] = field(default_factory=dict)  # platform key -> download
    license: str = ""
    homepage: str = ""

    @property
    def folder(self) -> str:
        return f"{self.name}-{self.version}"


FFMPEG = Tool(
    name="ffmpeg",
    label="FFmpeg",
    version="9.0.2",
    programs={"ffmpeg": "bin/ffmpeg", "ffprobe": "bin/ffprobe"},
    downloads={
        "windows-x86_64": Download(
            url="https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip",
            sha256="60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba",
            size=114768076,
            kind="zip",
            inner="ffmpeg-9.0.2-essentials_build",
        ),
    },
    license="GPL-3.0 (FFmpeg essentials build from gyan.dev)",
    homepage="https://www.gyan.dev/ffmpeg/builds/",
)

DEEPFILTER = Tool(
    name="deep-filter",
    label="DeepFilterNet",
    version="0.5.6",
    programs={"deep-filter": "deep-filter"},
    downloads={
        "windows-x86_64": Download(
            url="https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-x86_64-pc-windows-msvc.exe",
            sha256="75e11fa16445f560cb6b021521ddb89e89270d13b83089705d98776f58fd7915",
            size=26912256,
            kind="exe",
        ),
        "linux-x86_64": Download(
            url="https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-x86_64-unknown-linux-musl",
            sha256="70775e251eee44c0f2451a1e833326cf8bcbbe304d3e7cd12851e6fce72ef7da",
            size=36417296,
            kind="exe",
        ),
    },
    license="MIT or Apache-2.0",
    homepage="https://github.com/Rikorose/DeepFilterNet",
)

TOOLS = {t.name: t for t in (FFMPEG, DEEPFILTER)}
_OWNER = {program: tool for tool in TOOLS.values() for program in tool.programs}


def platform_key() -> str:
    system = "windows" if os.name == "nt" else platform.system().lower()
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    return f"{system}-{machine}"


def tools_dir() -> Path:
    """Where tools are installed; CUTZO_TOOLS overrides it (tests)."""
    return Path(os.environ.get("CUTZO_TOOLS") or TOOLS_DIR)


def _exe(path: Path) -> Path:
    return path.with_name(path.name + ".exe") if os.name == "nt" else path


def installed_path(program: str) -> Path:
    tool = _OWNER[program]
    return _exe(tools_dir() / tool.folder / tool.programs[program])


def find(program: str) -> Path | None:
    """The program Cutzo should run: its own pinned copy first, then PATH."""
    if program not in _OWNER:
        raise KeyError(program)
    own = installed_path(program)
    if own.is_file():
        return own
    if os.environ.get("CUTZO_TOOLS_ONLY"):
        return None
    found = shutil.which(program)
    return Path(found) if found else None


def require(program: str) -> Path:
    path = find(program)
    if path is None:
        tool = _OWNER[program]
        raise CutzoError(f"{tool.label} isn't installed yet, and Cutzo needs it for this.",
                         hint="Double-click “Update Cutzo” in the Cutzo folder; it installs it.")
    return path


def status() -> list[dict[str, object]]:
    """One line per tool for the self-test."""
    out = []
    for tool in TOOLS.values():
        paths = {p: find(p) for p in tool.programs}
        ok = all(paths.values())
        own = all(installed_path(p).is_file() for p in tool.programs)
        out.append({
            "tool": tool.name,
            "label": tool.label,
            "version": tool.version,
            "ok": ok,
            "own_copy": own,
            "paths": {p: str(v) if v else None for p, v in paths.items()},
        })
    return out
