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
