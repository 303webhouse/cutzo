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
run.pyw                 windowless entry point: <.python pythonw.exe> run.pyw [engine]. The
                        .venv's pythonw.exe from uv's Python build is a CONSOLE program (it opens
                        a black window), so shortcut and task use the real pythonw.exe + run.pyw
cutzo/connector_install.py  adds/removes "cutzo" in claude_desktop_config.json (with backup)
scripts/setup.ps1, scripts/update.ps1  + Setup Cutzo.bat / Update Cutzo.bat (CRLF, ASCII only)
```

- MCP SDK v2 renamed FastMCP to `mcp.server.mcpserver.MCPServer`. Raise
  `mcp.server.mcpserver.exceptions.ToolError` for messages Claude should see; other
  exceptions reach Claude as a generic error.
- One engine process owns state and the job queue, so the screens and Claude always agree.
  The screens poll `/api/state` every 1.5 s and toast changes made by Claude.
- Core modules: `kit.py` (show-kit inventory from Drive, plus `local_copy` of a kit file as
  working audio), `shows.py` (templates and slots), `presets.py` (export targets, rules and
  the `spec` each finished file is checked against), `media.py` (StreamYard/GH5 file names),
  `timeline.py` (cut arithmetic), `transcript.py`, `jobs.py`, `activity.py`, `safety.py`,
  `sample.py` (Phase 1 sample episodes and the synthesized test clips).
- Phase 2 (real audio) modules:
  - `tools/` one wrapper per outside program: `ffmpeg.py` (probe, decode, loudness, run with
    progress and cancel, chapter metadata), `deepfilter.py` (DeepFilterNet noise removal).
    `tools/__init__.py` pins each download (URL, size, sha256, licence); `python -m
    cutzo.tools.install` fetches them into `<app>/tools` (setup.ps1 runs it). `tools.find()`
    prefers Cutzo's own copy, then PATH. `CUTZO_TOOLS` moves the folder (tests).
  - `core/audio.py` numpy maths on working audio: raw 32-bit float, 48 kHz, no header
    (`.f32`), memory-mapped so long episodes never fill memory. Loudness outline, waveform
    peaks, silence finding, dead-air cuts, lining recordings up by sound.
  - `core/voice.py` the Podcast voice chain (rumble filter, DeepFilterNet in 300 s pieces on
    several workers with crossfaded joins, de-esser, compressor, leveller) and its settings.
    Strength 25 ("light") is the default: Nick picked it in a blind listening test (Oct 14).
  - `core/build.py` the real build: choose sources (StreamYard mix vs separate tracks), decode,
    line up, voice cleanup, previews (`preview.m4a`, `original.m4a`), dead-air suggestions.
  - `core/recipes.py` each show's layout, copied from Nick's Premiere project (The Dig: intro
    theme under the cold open, bites, stinger before each chapter, sign-off, outro), with kit
    levels relative to the talk loudness.
  - `core/render.py` turns a layout into a stereo mix (music dips by `Plan.duck_db` while a voice
    plays over it), encodes it (MP3 with chapters and tags, gain plus limiter to the target
    loudness, one fine-tune pass) and checks the file.
- Episode folders hold `voice.f32` (cleaned talk, recording time), `original.f32`, the two
  previews and `waveform-*.json` caches. Exports render `mix.f32` then delete it.

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
- Two looks, dark (the default) and light: `js/theme.js` (a plain script in `<head>`) sets
  `data-theme` on `<html>` before the page draws, from the browser-saved choice (dark, light or
  match Windows). In `app.css` use the named colours (`var(--ink)` and so on), never raw ones,
  except on the deck, which is dark in both looks. Check new screens in both looks.
- Windows scripts stay ASCII with CRLF line endings (`.gitattributes` keeps them as committed).

## Sample episodes vs real episodes

Episodes marked `sample` still use the Phase 1 pretend steps (`_apply_sync`, `_apply_voice`,
`_apply_transcribe`, `_apply_auto_edit`, `_export_work`, `_mock_check`). Real episodes go
through `_real_build` and `_real_export_work`. The transcribe stage is `"later"` for real
episodes until Phase 3 (whisper.cpp); stages treat "later" like done. Video export is refused
for real episodes until Phase 3. New outside programs get their own wrapper in `cutzo/tools/`.

## Running things

```
uv venv --python 3.12 --python-preference only-managed .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m cutzo.tools.install   (FFmpeg and DeepFilterNet into ./tools)
.venv/bin/python -m pytest             (all tests, about 3 minutes; real-audio tests skip
                                        themselves when FFmpeg isn't installed)
.venv/bin/python -m cutzo.engine --speed 0.1   (engine with 10x faster pretend jobs)
```

Environment variables: `CUTZO_HOME` (library folder), `CUTZO_PORT`, `CUTZO_SPEED`,
`CUTZO_DRIVE` (Google Drive folder with the show kits), `CUTZO_TOOLS` (tools folder),
`CUTZO_TOOLS_ONLY` (ignore programs on PATH).
`tests/test_connector.py` runs the real connector over stdio and lets it start an engine.

## Nick's PC (checked Oct 13, 2026)

- Windows, Claude desktop app (Microsoft Store/MSIX build) whose settings file is
  `%APPDATA%\Claude\claude_desktop_config.json` (other connectors there: tradingview,
  railway-mcp-server; Desktop Commander and PDF Tools are extensions).
- uv 0.11 at `%USERPROFILE%\.local\bin\uv.exe`. Setup installs Cutzo's own Python into
  `F:\Cutzo\app\.python` (UV_PYTHON_INSTALL_DIR) and builds `.venv` from it.
- AppData traps: commands run through Desktop Commander run INSIDE the Claude app's MSIX
  container, where writes to AppData go to a private copy
  (`%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\...`) that the desktop shortcut and
  Task Scheduler can't see. `Roaming\Claude` is the exception (real). So never put anything
  Cutzo needs at runtime in AppData; keep it under F:\Cutzo. The Microsoft Store system Python
  has the same kind of redirection: never build Cutzo's .venv from it.
- git 2.53 and gh (signed in as 303webhouse). Library `F:\Cutzo`, app `F:\Cutzo\app`.
- Free space: C: about 75 GB (never write media there), F: about 710 GB.
- Google Drive for desktop at `G:\My Drive` (show kits under `Denver Stiffs\The Dig` etc.).
- Graphics card: NVIDIA GeForce RTX 2070 SUPER (CUDA works with torch 2.5.1+cu124). DeepFilterNet
  runs on the processor; Cutzo doesn't need the graphics card yet. `F:\Cutzo\lab` is a scratch area for
  experiments: `cutzo-dev` (a clone of the repo), `models-venv` (Resemble Enhance and
  VoiceFixer for the listening test), `listen\index.html` (the listening test page).
  `lab\bg.ps1 -Script x.py` runs a lab script in the background with Cutzo's Python and
  writes `x.log`/`x.err`, because Desktop Commander gives up on commands after 60 seconds.

## Git

The Phase 1 skeleton and its install fixes went straight to `main` of the new empty repo.
From Phase 2 on, work on a branch and open a pull request. Commit messages end with the attribution
lines given in the session.
