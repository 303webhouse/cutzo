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
    levels relative to the talk loudness. The talk (`voice.f32`) is one channel and measured so,
    but plays in both stereo channels, 3 dB louder (`render.MONO_IN_STEREO_DB`); The New Heretics'
    levels come from Nick's stereo video (talk -16.0 LUFS), so its kit offset adds those 3 dB.
  - `core/render.py` turns a layout into a stereo mix (music dips by `Plan.duck_db` while a voice
    plays over it), encodes it (MP3 with chapters and tags, gain plus limiter to the target
    loudness, one fine-tune pass) and checks the file.
- Phase 3 (transcripts, video) modules:
  - `tools/parakeet.py` speech to text: NVIDIA's Parakeet TDT 0.6B v3 run by `parakeet-cli`
    from the pinned whisper.cpp release, on the graphics card. Long recordings go in as
    overlapping 48 s pieces that meet in pauses (the fast mode takes up to 50 s; the model is
    weaker near the ends of what it hears). Words carry `w`, `s`, `e`, `p`. It replaced Whisper
    on Oct 15: fillers without prompting, better word times (2% vs 6% in silence), 65x real time.
  - `core/cutpoints.py` where a word cut's edges go: both chosen together on the voice's
    loudness outline, near the gaps, and the stretch taken out must hold the words' sound
    (otherwise an "um" that runs into the next word gets missed).
  - `build.transcribe` makes the 16 kHz copy, runs Parakeet and works out who said what from
    the separate tracks. `transcript.json` sits in the episode folder.
  - The New Heretics' video (step 3, Oct 15): `recipes.NEW_HERETICS` copies Nick's Spooky Sode
    edit (teaser ends as the montage's own music starts; talk 83.7 s into the montage; music
    breaks before chapters with `segment: true`; a page flip, or a voiced segment title, at every
    other chapter; ending music + Full Flames under the sign-off picks; fade to black). His frame
    holds: the montage's frame at 87.354 s under the talk, its frame at 0.434 s under the ending,
    the intro's first frame under the teaser. The flame videos (BG Loop at 2.0314x, Music Loop, Full
    Flames) are flames on black, laid over that with Premiere's Screen blend (`Layer.blend`). It
    fills `render.Plan.picture` (layers bottom to top, spectrum windows) besides the sound.
    `core/video.py` composes it in one FFmpeg pass over a black canvas in planar RGB (gbrp), layers
    placed with `setpts`: normal ones with `overlay=eof_action=pass` (long fading layers split so
    only fades blend), screen ones with `blend=all_mode=screen` (their fades go to black); stills
    (title, spectrum colours, held frames) converted once and held with the `loop` filter; the
    spectrum fed through stdin (`ffmpeg.run(feed=...)`). The last step converts to BT.709 TV range
    and labels the frames with `setparams` (FFmpeg 9 ignores `-color_primaries`/`-color_trc` as
    output options). Encoding (`video.encoder_args`): a set quality (x264 CRF 17 for YouTube, 18
    for Substack; NVENC CQ 2 higher) under a ceiling (12 / 8 Mbps), 2 B-frames, closed GOP,
    keyframes every 15 frames. The picture is drawn without sound while a thread encodes and checks
    the sound (AAC at 384 kbps runs at only ~9x real time on one core), then `video.mux` puts them
    together. NVENC if the driver allows (Nick's 572 is too old for FFmpeg 9's NVENC: needs 610+),
    else libx264 `faster`: Spooky Sode (2 h 4 min) exports in 67 minutes, 10.4 GB at 11.7 Mbps
    (Nick's own Premiere file is 12 GB).
  - `render.encode` measures gain + limiter (a quick ebur128 pass, no encoding) and corrects the
    gain before encoding, so one encode lands on the target loudness.
  - `core/spectrum.py` After Effects' Audio Spectrum (settings from Nick's .aep), fitted to the
    finished JW Ghost Stories frames: per frame a fill and a rim alpha (stacked grey picture),
    colours as two still PNGs. `core/titles.py` the episode title graphic (Montserrat ExtraBold +
    SemiBold, bundled in `cutzo/assets/fonts` with the OFL licence), lines from
    `ep["video_title"]` or the episode title split at its colon.
  - `kit.video_copy` makes 1080p 29.97 fps x264 copies of kit videos once (`<library>/kit/video`),
    covering only what episodes use (the Music Loop is 64 min; only the start is needed), decoded
    on the processor (`-hwaccel auto` was twice as slow on Nick's PC). BG Loop's 71 min of 4K take
    about 20 minutes, once. Kit entries carry `seconds` so layouts work without the files.
  - `core/captions.py` (step 4): every real export also writes `<title> - Captions.srt` and
    `<title> - YouTube chapters.txt` next to the files (`ep["extras"]`). Words are placed by the
    plan's voice pieces, grouped 2 x 42 characters, 7 s at most, "um"/"uh" left out.
  - Extras (Oct 15; Nick picked these over a Kdenlive project for one-offs): sounds or videos for
    one episode, like the haiku in Spooky Sode's first break. Stored as `ep["inserts"]` (because
    `ep["extras"]` holds the captions files); core `add_insert`/`remove_insert`, routes `/extras`,
    connector `add_extra`/`remove_extra`, the Extras panel on Review. `recipes.extras_by_time`
    groups them by the recording time where the talk pauses; one placed at a chapter follows it and
    plays in its pause or break, after the break music and before the page flip or stinger (before
    extras, a long break cue plays alone for at most 22.1 s, then fades over 3.9 s, as Nick did).
    Sources are kit ids or "insert:<id>": the export makes working copies of just the part used in
    `<episode>/inserts/` (`<id>.f32`, `<id>-1080p.mp4` letterboxed by `kit.fit_1080p`). Levels:
    spoken and video as loud as the talk, music 3 dB below (The New Heretics: 3.2), kit cues at
    Nick's own level. The review player doesn't play extras; the finished file has them.
- Episode folders hold `voice.f32` (cleaned talk, recording time), `original.f32`, the two
  previews, `transcript.json`, `waveform-*.json` caches and `inserts/` (extras' copies). Exports
  render `mix.f32` then delete it (video exports also use `work-export/`, deleted after).

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
through `_real_build` and `_real_export_work`. The transcribe stage is `"later"` when speech to
text isn't installed (stages treat "later" like done; `transcribe` makes it later). Video
export works for The New Heretics at 1080p (`youtube-1080p`, `substack-video`); `youtube-4k` and
video for The Dig are refused. New outside programs get their own wrapper in `cutzo/tools/`.

## Running things

```
uv venv --python 3.12 --python-preference only-managed .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m cutzo.tools.install   (FFmpeg, DeepFilterNet and whisper.cpp into ./tools;
                                          the Parakeet model is Windows-only: tests use
                                          whisper.cpp's tiny for-tests-ggml-parakeet-tdt.bin
                                          from raw.githubusercontent.com, put in CUTZO_TOOLS)
.venv/bin/python -m pytest             (all tests, about 3 minutes; real-audio tests skip
                                        themselves when FFmpeg isn't installed)
.venv/bin/python -m cutzo.engine --speed 0.1   (engine with 10x faster pretend jobs)
```

Environment variables: `CUTZO_HOME` (library folder), `CUTZO_PORT`, `CUTZO_SPEED`,
`CUTZO_DRIVE` (Google Drive folder with the show kits), `CUTZO_TOOLS` (tools folder),
`CUTZO_TOOLS_ONLY` (ignore programs on PATH), `CUTZO_PARAKEET_MODEL` (speech model file).
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
- Graphics card: NVIDIA GeForce RTX 2070 SUPER, 8 GB (CUDA works with torch 2.5.1+cu124 and
  whisper.cpp's CUDA 12.4 build). Speech to text runs on it; DeepFilterNet runs on the processor.
  `F:\Cutzo\lab` is a scratch area for
  experiments: `cutzo-dev` (a clone of the repo), `models-venv` (Resemble Enhance, VoiceFixer,
  matplotlib), `listen\index.html` (the listening test page), `whisper\` and `align\` (the
  speech-to-text and word-timing trials of Oct 15, with `align_compare.py`, `cut_variants.py`
  and `filler_plot.py` to measure and draw them).
  The video step's lab work (Oct 15): `aep_dump.py`/`aep_spect.py` read After Effects projects,
  `spec_calib*.py` and `look_compare2.py` fit and compare the spectrum against JW Ghost Stories
  frames, `title_fit.py`/`stem_width.py` measured the title graphic, `tnh_levels.py` and
  `tnh_gain_curve.py` the music levels, `intro_compare.py`/`find_in.py` line sounds up.
  `spooky_start.py`/`spooky_edit.py` make the real Spooky Sode episode with Nick's own cuts;
  `add_haiku.ps1` adds his haiku piece to it as extras. `spooky_compare.py <video>` lines Cutzo's
  export up with JW Ghost Stories by sound and compares levels and frames (`compare\`);
  `after_export.ps1 -Job <id>` waits for an export job, then runs it (and `render_bench.py`).
  `plan_dump.py [from to]` prints Cutzo's layout; `render_bench.py` / `ending_bench.py` draw only
  the first 4 minutes / the ending with the current code. `spooky_layout.txt`/`spooky_params.txt`
  are Nick's Premiere project read out (clip times, effects; frame holds are `<FrameHold>` and
  `<FrameHoldStart>` in the .prproj, in ticks of 1/254016000000 s).
  `lab\bg.ps1 -Script x.py` runs a lab script in the background with Cutzo's Python and
  writes `x.log`/`x.err`, because Desktop Commander gives up on commands after 60 seconds.
  `lab\switch_branch.ps1 -Branch b` puts the app on a branch, runs setup and restarts the engine
  (start it hidden with Start-Process; setup can take minutes).

## Git

The Phase 1 skeleton and its install fixes went straight to `main` of the new empty repo.
From Phase 2 on, work on a branch and open a pull request. Commit messages end with the attribution
lines given in the session.
