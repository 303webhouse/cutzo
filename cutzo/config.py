"""Where Cutzo keeps its files and which port the engine uses.

Order of precedence:
  1. environment variables CUTZO_HOME / CUTZO_PORT (used by tests)
  2. settings.json next to the app (written by the setup script)
  3. defaults: F:\\Cutzo on Windows when the F: drive exists, else ~/Cutzo
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
APP_DIR = PACKAGE_DIR.parent
SETTINGS_FILE = APP_DIR / "settings.json"
WEB_DIR = PACKAGE_DIR / "web"
DEFAULT_PORT = 8765
HOST = "127.0.0.1"

# Calls to Cutzo's own engine on this computer must never go through a proxy
# (a Windows proxy setting would otherwise swallow them).
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class SettingsError(Exception):
    """settings.json exists but can't be used; say so instead of quietly using defaults."""


@dataclass(frozen=True)
class Settings:
    library: Path
    port: int = DEFAULT_PORT
    host: str = HOST
    drive: Path | None = None  # Google Drive for desktop, where the show kit lives

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def default_library() -> Path:
    if os.name == "nt" and Path("F:/").exists():
        return Path("F:/Cutzo")
    return Path.home() / "Cutzo"


def default_drive() -> Path | None:
    """Google Drive for desktop's "My Drive" folder, if this computer has one."""
    for candidate in (Path("G:/My Drive"), Path.home() / "Google Drive" / "My Drive", Path.home() / "My Drive"):
        if os.name != "nt" and str(candidate).startswith("G:"):
            continue
        if candidate.is_dir():
            return candidate
    return None


def read_settings_file(path: Path | None = None) -> dict:
    path = path or SETTINGS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (OSError, ValueError) as exc:
        raise SettingsError(
            f"{path} has a mistake in it ({exc}). Fix it, or delete it and run Setup Cutzo again. "
            "Tip: folder paths need double backslashes there, like F:\\\\Cutzo."
        ) from None
    if not isinstance(data, dict):
        raise SettingsError(f"{path} should hold settings in curly brackets. Delete it and run Setup Cutzo again.")
    return data


def load_settings() -> Settings:
    data = read_settings_file()
    library = os.environ.get("CUTZO_HOME") or data.get("library_folder") or default_library()
    port_text = os.environ.get("CUTZO_PORT") or data.get("port") or DEFAULT_PORT
    try:
        port = int(port_text)
        if not 1024 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        raise SettingsError(f"The port in settings.json must be a number from 1024 to 65535, not {port_text!r}.") from None
    drive = os.environ.get("CUTZO_DRIVE") or data.get("drive_folder")
    return Settings(library=Path(str(library)).expanduser(), port=port,
                    drive=Path(str(drive)).expanduser() if drive else default_drive())
