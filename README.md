# Cutzo

Cutzo is Nick's own podcast and video editor for **The Dig** and **The New Heretics**.
It takes raw recordings to finished files for YouTube, Substack and the RSS feed, and
Claude can run it from the Claude desktop app to take care of repetitive edits.

The plan, decisions and to-do list live in the
[Cutzo Plan doc](https://claude.ai/code/artifact/8fcefec4-863e-4734-9bc4-6c4fe7401fe5).

## Where it stands: Phase 1, practice mode

Every screen works and the Claude connector works, but the episodes are **samples** and
the media work is pretend. Cutzo never touches your real files in this phase.

| Works for real now | Pretend until later phases |
| --- | --- |
| Cuts, bringing cuts back, undo | Lining up recordings (Phase 2) |
| Chapters and YouTube chapter lists | Noise, echo and voice cleanup (Phase 2) |
| Camera choices and show-kit swaps | Transcription (Phase 3) |
| Safety rules and the activity log | Rendering and exporting files (Phase 2 and 3) |
| Claude connector (20 tools) | Checking finished files with FFmpeg (Phase 2) |

## Install on Windows

1. Put the Cutzo folder at `F:\Cutzo\app` (Claude can do this for you with git).
2. Double-click **Setup Cutzo.bat** in that folder. It takes a minute or two the first time.
   It makes a **Cutzo** shortcut on your desktop and opens Cutzo in your browser.
3. Quit the Claude desktop app completely and open it again, so it loads the Cutzo connector.

Setup is safe to run again whenever something seems broken.

## Everyday use

- **Open Cutzo:** double-click **Cutzo** on your desktop. It opens in your browser at
  `http://127.0.0.1:8765`.
- **Quit Cutzo:** Settings, then Quit Cutzo. (Claude can start it again whenever it needs it.)
- **Get the newest version:** double-click **Update Cutzo.bat**.
- **Ask Claude**, in the Claude desktop app:
  - "List my Cutzo episodes."
  - "In Spooky Sode, cut the part where they say 'Of course it won't'."
  - "Read the Season Debrief transcript and suggest chapter titles."
  - "Build Episode 1, then export it for YouTube."

## Where things live

| What | Where |
| --- | --- |
| The app | `F:\Cutzo\app` |
| Episodes (project files, previews) | `F:\Cutzo\episodes` |
| Finished files | `F:\Cutzo\exports` |
| Copies of the show kit (from Phase 2) | `F:\Cutzo\kit` |
| Activity log | `F:\Cutzo\activity.jsonl` |
| Error details | `F:\Cutzo\logs\engine.log` |
| Settings (library folder, port) | `F:\Cutzo\app\settings.json` |

## Safety rules

1. Cutzo reads your original recordings and never changes or deletes them.
2. It writes only inside its own folders in `F:\Cutzo`.
3. Replacing a finished export, or making a very long cut, needs a second "yes".
4. Every change, by you, Claude or Cutzo itself, is written to the activity log.

## If something goes wrong

1. In Cutzo, open **Settings** and press **Run self-test**.
2. Still stuck? Copy the error into Claude. Details are in `F:\Cutzo\logs\engine.log`.

## For Claude and developers

See [CLAUDE.md](CLAUDE.md) for how the code is organised, how to run the tests and the
rules to follow when changing it.
