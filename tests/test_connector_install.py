"""Registering the connector in claude_desktop_config.json without disturbing anything else."""

import json

import pytest

from cutzo import connector_install as ci


def _config(tmp_path, data, raw=None):
    path = tmp_path / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir(parents=True)
    path.write_text(raw if raw is not None else json.dumps(data), encoding="utf-8")
    return path


def test_adds_cutzo_and_keeps_everything_else(tmp_path):
    original = {
        "mcpServers": {
            "tradingview": {"command": "C:\\Users\\nickh\\.local\\bin\\uvx.exe", "args": ["tradingview-mcp"]},
            "railway-mcp-server": {"command": "npx", "args": ["-y", "@railway/mcp-server"]},
        },
        "preferences": {"menuBarEnabled": False, "note": "caf\u00e9"},
    }
    path = _config(tmp_path, original)
    msgs = ci.install([path], stamp="20261013-120000")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["cutzo"]["args"] == ["-m", "cutzo.mcp_server"]
    assert data["mcpServers"]["tradingview"] == original["mcpServers"]["tradingview"]
    assert data["preferences"] == original["preferences"]
    assert (path.parent / "claude_desktop_config.cutzo-backup-20261013-120000.json").exists()
    assert "untouched: railway-mcp-server, tradingview" in msgs[0]
    again = ci.install([path], stamp="20261013-120001")
    assert "already set up" in again[0]


def test_handles_bom_and_missing_servers(tmp_path):
    path = _config(tmp_path, None, raw="\ufeff{\"globalShortcut\": \"Ctrl+Space\"}")
    ci.install([path], stamp="x")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["globalShortcut"] == "Ctrl+Space" and "cutzo" in data["mcpServers"]


def test_refuses_broken_file(tmp_path):
    path = _config(tmp_path, None, raw="{ not json")
    with pytest.raises(SystemExit):
        ci.install([path], stamp="x")
    assert path.read_text(encoding="utf-8") == "{ not json"


def test_creates_file_when_missing(tmp_path):
    path = tmp_path / "Claude" / "claude_desktop_config.json"
    ci.install([path], stamp="x")
    assert json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["cutzo"]


def test_remove(tmp_path):
    path = _config(tmp_path, {"mcpServers": {"other": {"command": "x"}}})
    ci.install([path], stamp="a")
    ci.install([path], remove=True, stamp="b")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "cutzo" not in data["mcpServers"] and "other" in data["mcpServers"]


def test_backups_are_pruned(tmp_path):
    path = _config(tmp_path, {"mcpServers": {}})
    for n in range(8):
        ci.install([path], remove=bool(n % 2), stamp=f"2026101{n}")
    assert len(list(path.parent.glob("claude_desktop_config.cutzo-backup-*.json"))) <= ci.KEEP_BACKUPS


def test_windowless_python_comes_from_pyvenv_cfg(tmp_path):
    from cutzo import starter

    base = tmp_path / "python" / "cpython-3.12"
    base.mkdir(parents=True)
    (base / "pythonw.exe").write_bytes(b"MZ")
    venv = tmp_path / "app" / ".venv"
    venv.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(f"home = {base}\nversion_info = 3.12.13\n", encoding="utf-8")
    assert starter.base_pythonw(tmp_path / "app") == base / "pythonw.exe"
    (venv / "pyvenv.cfg").write_text("version_info = 3.12.13\n", encoding="utf-8")
    assert starter.base_pythonw(tmp_path / "app") is None
    cmd = starter.windowless_command("engine")  # off Windows: the plain module command
    assert cmd[-2:] == ["-m", "cutzo.engine"]
