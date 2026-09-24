# Run it on your own computer (live Studio on localhost)

Everything runs locally: the Studio on http://localhost:5173 and the pipeline in a terminal.
The Studio polls the feed folder every few seconds, so you watch each stage, the script, the
footage and the renders appear live — no deploys. Your own internet connection also avoids the
cloud-IP blocks we hit (Wikimedia's robot policy, YouTube's bot check).

## Once
```bash
git clone https://github.com/withinhua/Documentary && cd Documentary
git checkout claude/zealous-davinci-lerpgn
./scripts/setup-local.sh          # Python venv, voice model, Studio deps (~10 min first time)
```
Needs: Python 3.10+, ffmpeg, Node 20+ (`brew install python ffmpeg node` on a Mac).

## Every time
```bash
# terminal 1 — the Studio
cd editor && pnpm dev             # open http://localhost:5173

# terminal 2 — make the New Coke video and watch it build in the Studio
./scripts/new-coke.sh
```
The finished video lands in `out/new-coke/final.mp4`; "Open in editor" in the Studio turns it
into an editable timeline.

## With Claude Code locally
Open the folder in Claude Code (desktop app or `claude` in a terminal) and ask, e.g.
"run /documentary on the New Coke project" or "make a documentary about the Ford Pinto". The
`documentary` skill in `.claude/skills/` walks through script → footage → review → render.

## Windows (PowerShell, no WSL needed)
```powershell
winget install Git.Git OpenJS.NodeJS.LTS GitHub.cli     # then close and reopen PowerShell
gh auth login
gh repo clone withinhua/Documentary; cd Documentary; git checkout claude/zealous-davinci-lerpgn
powershell -ExecutionPolicy Bypass -File scripts\studio.ps1        # open http://localhost:5173
```
To make videos too: `winget install Python.Python.3.12 Gyan.FFmpeg`, reopen PowerShell, then
`powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1` once and
`powershell -ExecutionPolicy Bypass -File scripts\new-coke.ps1` in a second window.

## The desktop app (Windows installer)
Every push that changes `editor/` builds **DocumentaryStudio-<version>-x64.exe** on GitHub's Windows
machines and publishes it on the repo's Releases page as **studio-latest**. Install it, open
"Documentary Studio", and click **Choose your Documentary folder** (your checkout of this repo).
The dashboard then shows your productions live; each production has **Get footage**,
**Make video** and **Show file** buttons that run the pipeline on your PC (after
`scripts\setup-local.ps1` has set up Python and the voice model once).

## Connect Claude Code to the app (MCP)
With Documentary Studio open, from this folder in PowerShell:
```powershell
$e = Get-Content "$HOME\.openreel\mcp-endpoint.json" | ConvertFrom-Json
claude mcp add --transport http documentary-studio $e.url --header "Authorization: Bearer $($e.token)"
claude mcp list        # documentary-studio should show ✓ Connected
```
If it stops connecting after the app restarts: `claude mcp remove documentary-studio`, then the two lines above again.
