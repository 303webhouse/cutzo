# Cutzo: notes for Claude

Read this before changing anything. The plan, decisions and to-do list are in the
[Cutzo Plan doc](https://claude.ai/code/artifact/8fcefec4-863e-4734-9bc4-6c4fe7401fe5)
(tabs: Plan, To-Do, Build Notes). Session routine: read the Plan and To-Do tabs first;
at the end tick finished tasks, add new ones, record decisions in the Plan tab.

## Working with Nick

- He doesn't code. Talk in plain English, no jargon or shorthand.
- He has ADHD: give short, clear, numbered steps and say exactly what he must decide.
  Keep the To-Do tab current so context isn't lost.
- Free tools only for now. Mock-data skeletons before live data.
- Windows PC: PowerShell chains commands with `;`, not `&&`.

## Architecture

```
cutzo/core/service.py   Cutzo class: EVERY action is a method here (the only place logic lives)
cutzo/engine.py         FastAPI app on 127.0.0.1:8765, thin routes over Cutzo methods
cutzo/web/              browser screens: vanilla JS modules, no build step, fonts bundled
cutzo/mcp_server.py     Claude connector (MCP Python SDK v2 `MCPServer`, stdio). Each tool calls
                        the engine over HTTP and starts the engine if it isn't running
cutzo/launcher.py       what the desktop shortcut runs (pythonw -m cutzo)
cutzo/starter.py        starts the engine so it outlives its starter: on Windows through the
                        on-demand Task Scheduler task "Cutzo Engine" (made by setup), because
                        the connector runs inside a job object that kills its children
cutzo/connector_install.py  adds/removes "cutzo" in claude_desktop_config.json (with backup)
scripts/setup.ps1, scripts/update.ps1  + Setup Cutzo.bat / Update Cutzo.bat (CRLF, ASCII only)
```

- MCP SDK v2 renamed FastMCP to `mcp.server.mcpserver.MCPServer`. Raise
  `mcp.server.mcpserver.exceptions.ToolError` for messages Claude should see; other
  exceptions reach Claude as a generic error.
- One engine process owns state and the job queue, so the screens and Claude always agree.
  The screens poll `/api/state` every 1.5 s and toast changes made by Claude.
- Core modules: `kit.py` (show-kit inventory from Drive), `shows.py` (templates and slots),
  `presets.py` (export targets and rules), `media.py` (StreamYard/GH5 file names),
  `timeline.py` (cut arithmetic), `transcript.py`, `jobs.py`, `activity.py`, `safety.py`,
  `sample.py` (Phase 1 sample episodes and the synthesized test clips).

## Rules to keep

- New features go in `Cutzo` first, then a route in `engine.py`, then a tool in
  `mcp_server.py` if Claude should have it, then the screen. Never put logic in a route.
- Every change takes `source` ("you", "claude", "cutzo") and is logged with `_log`;
  Claude's look-ups are logged with `_log_read`. Refusals are logged by the engine.
- Write files only through `self.guard.writable(...)`. Originals are read-only, always.
- Times in the API are recording time (the original recording). Convert to finished-file
  time with `timeline.to_output` (chapters for YouTube do this).
- Edits are non-destructive records in `project.json`; undoable changes push to `history`.
- Errors: raise `CutzoError(message, hint=...)` with a plain-English message.
  `NeedsConfirmation` (409) for anything that replaces finished work or cuts a lot.
- Engine security: only 127.0.0.1/localhost Host headers; non-GET requests need the
  `X-Cutzo-Source` header and a local Origin; responses forbid framing. Don't add CORS.
- The engine claims its port before building `Cutzo`, so two engines can never share a library.
  Local HTTP calls use `config.LOCAL` (no proxy). A broken settings.json raises `SettingsError`.
- The screens redraw only for changes made elsewhere (Claude, finished jobs) and keep
  anything half-typed (paths, chapter titles, export ticks, the "replace?" question).
- No CDNs or web fonts from the internet; the app must work offline.
- Windows scripts stay ASCII with CRLF line endings (`.gitattributes` keeps them as committed).

## Phase 1 pretend parts and where Phase 2 plugs in

`Cutzo._apply_sync`, `_apply_voice`, `_apply_transcribe`, `_apply_auto_edit` (called by
`_build_work`) and `_export_work` / `_mock_check` are the pretend steps. Replace them with
FFmpeg, whisper.cpp and the voice-cleanup models behind small wrapper modules (one module
per external tool, so each can be swapped). Keep the method names and the job steps.

## Running things

```
uv venv --python 3.12 --python-preference only-managed .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest             (all tests, about 30 seconds)
.venv/bin/python -m cutzo.engine --speed 0.1   (engine with 10x faster pretend jobs)
```

Environment variables: `CUTZO_HOME` (library folder), `CUTZO_PORT`, `CUTZO_SPEED`.
`tests/test_connector.py` runs the real connector over stdio and lets it start an engine.

## Nick's PC (checked Oct 13, 2026)

- Windows, Claude desktop app (Microsoft Store/MSIX build) whose settings file is
  `%APPDATA%\Claude\claude_desktop_config.json` (other connectors there: tradingview,
  railway-mcp-server; Desktop Commander and PDF Tools are extensions).
- uv 0.11 at `%USERPROFILE%\.local\bin\uv.exe` with managed CPython 3.12 and 3.14.
  System Python is the Microsoft Store build: never build Cutzo's .venv from it (it silently
  redirects AppData writes, so Claude would never see the connector). Setup rebuilds such a .venv.
- git 2.53 and gh (signed in as 303webhouse). Library `F:\Cutzo`, app `F:\Cutzo\app`.
- Free space: C: about 75 GB (never write media there), F: about 710 GB.

## Git

The Phase 1 skeleton and its install fixes went straight to `main` of the new empty repo.
From Phase 2 on, work on a branch and open a pull request. Commit messages end with the attribution
lines given in the session.
