"""The Claude connector, end to end: a real MCP client talks to `python -m cutzo.mcp_server`
over stdio, and the connector starts the engine by itself (as it will on Nick's PC)."""

import asyncio
import json
import os
import sys
import urllib.request

import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client

EXPECTED_TOOLS = {
    "list_shows", "list_episodes", "get_episode", "create_episode", "import_media", "build_episode",
    "job_status", "get_transcript", "cut_section", "cut_words", "restore_cut", "undo_last_change",
    "set_camera", "add_chapter", "swap_asset", "list_assets", "export", "check_export",
    "get_activity_log", "show_in_cutzo", "add_highlight", "remove_highlight", "set_dead_air",
    "set_voice_strength", "transcribe_episode", "mark_segment", "set_title_graphic",
}


def _shutdown(port):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}", method="POST",
                                     headers={"X-Cutzo-Source": "app", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass


def test_connector_round_trip(tmp_path, free_port):
    env = dict(os.environ, CUTZO_HOME=str(tmp_path / "Cutzo"), CUTZO_PORT=str(free_port), CUTZO_SPEED="0.02")
    params = StdioServerParameters(command=sys.executable, args=["-m", "cutzo.mcp_server"], env=env)

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                init = await s.initialize()
                assert init.server_info.name == "Cutzo"
                tools = {t.name for t in (await s.list_tools()).tools}
                assert tools == EXPECTED_TOOLS

                async def call(name, args=None):
                    res = await s.call_tool(name, args or {})
                    text = res.content[0].text if res.content else ""
                    return res.is_error, text

                err, text = await call("list_episodes")  # starts the engine on first use
                assert not err, text
                assert "Spooky Sode" in text

                err, text = await call("get_transcript", {"episode": "spooky"})
                assert not err and "[0:00] Host:" in text

                err, text = await call("cut_words", {"episode": "spooky", "text": "Of course it won't"})
                assert not err, text
                assert json.loads(text)["cut_ids"]

                err, text = await call("cut_section", {"episode": "spooky", "start": "0:40", "end": "0:30"})
                assert err and "must be after" in text

                err, text = await call("add_chapter", {"episode": "spooky", "time": 30, "title": "Define Your Terms"})
                assert not err and "Define Your Terms.mp3" in text

                err, text = await call("undo_last_change", {"episode": "spooky"})
                assert not err and "Undid" in text

                err, text = await call("export", {"episode": "dig", "targets": ["podcast-mp3"]})
                assert not err, text
                job_id = json.loads(text)["job"]["id"]
                for _ in range(100):
                    err, text = await call("job_status", {"job_id": job_id})
                    if json.loads(text)["status"] not in ("queued", "running"):
                        break
                    await asyncio.sleep(0.1)
                assert json.loads(text)["status"] == "done"

                err, text = await call("export", {"episode": "dig", "targets": ["podcast-mp3"]})
                assert err and "confirm=true" in text

                err, text = await call("get_activity_log", {"limit": 10})
                who = {e["who"] for e in json.loads(text)["entries"]}
                assert "Claude" in who and "Cutzo" in who

    try:
        asyncio.run(asyncio.wait_for(run(), timeout=120))
    finally:
        _shutdown(free_port)


@pytest.mark.parametrize("bad_port", [1])
def test_connector_explains_when_engine_cannot_start(tmp_path, bad_port):
    """With an unusable port the connector gives a plain-English error instead of hanging forever."""
    from cutzo.mcp_server import Engine

    engine = Engine()
    engine.base = f"http://127.0.0.1:{bad_port}"
    assert engine.healthy(timeout=0.2) is False
