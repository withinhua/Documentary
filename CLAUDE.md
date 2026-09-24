# Documentary Studio — briefing for Claude Code

Topic in → long-form YouTube documentary out, in a paper-collage style (halftone photo cut-outs with
sticker borders, torn orange number cards, typewriter labels, archival footage in between).
The owner is non-technical, on Windows, sometimes on mobile data: keep downloads small, explain
steps plainly, and prefer doing things over describing them.

## What's where
| Path | What |
|---|---|
| `projects/<slug>/` | One production: `research.md`, `script.json` (chapters → beats: narration, `scene`, `request`), `requests.json`, `footage/` (candidates, picks, media), `status.json` |
| `pipeline/produce.py` | Narration (Kokoro) → one collage scene per beat → final cut → Studio feed |
| `pipeline/feed.py`, `pipeline/live.py`, `pipeline/status.py` | Publish productions + live stage progress to the Studio |
| `footage/` | Free-archive search (`footage.find`), review protocol (`footage/REVIEW.md`), download (`footage.fetch`) |
| `collage/` | The scene renderer (scene types: cutout, number_card, full, quote, headline, title) |
| `editor/` | Our fork of the OpenReel editor (MIT): Studio dashboard in `apps/web/src/studio/`, desktop app in `apps/desktop/` |
| `.claude/skills/documentary/SKILL.md` | The end-to-end workflow: script → find footage → review with vision → fetch → produce |
| `docs/` | Design docs; `docs/LOCAL.md` = running locally |

## Running things (Windows PowerShell, from this folder)
- One-time setup: `powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1`
- Make New Coke: `powershell -ExecutionPolicy Bypass -File scripts\new-coke.ps1`
- Python is `.\.venv\Scripts\python.exe`; set `$env:KOKORO_DIR="$PWD\models\kokoro"` before `pipeline.produce`.
- Results: `out\<slug>\final.mp4`; progress shows live in the Documentary Studio app's dashboard.

## The app (MCP)
The Documentary Studio desktop app runs a local MCP server (`documentary-studio`, 300+ tools):
create projects, import media (`import_media_from_url` / files), add clips/text/effects/keyframes/
transitions/markers, `get_project_manifest`, export. Use it to build and adjust timelines live
while the owner watches. If it stops connecting, the app restarted with a new port/token:
re-run the `claude mcp remove/add` lines in `docs/LOCAL.md`.

## Rules
- Footage: free licences first; fair use only as a last resort (≤ 8 s clips of the real subject,
  the narration comments on it), always recorded in `footage/sources.json`. Never fill gaps with
  unrelated footage — use the scripted fallback card instead.
- Facts: every claim traceable to `research.md`; paraphrased headlines are never shown as real newspapers.
- Respect site rate limits and robot policies; never spoof browsers or bypass bot checks.
- Commit work to the current branch with clear messages.

## Current state (New Coke, `projects/new-coke`)
78 beats (~7.5 min). 30/51 picture beats have approved footage (`footage/picks.json`, some fair
use); 21 use typographic fallback cards. Gaps: photos of Goizueta, Dyson, Zyman, Mullins; the
protests; the 1985 "New Taste" can. Next: fetch picks at home (YouTube clips work there), render,
review frames, then improve gaps.
Paused work: faster collage rendering (`collage/`, `pipeline/produce.py` cache/streaming) and the
instant media library (`library/`) — both committed as work-in-progress.
