---
name: documentary
description: Make a documentary end to end from a topic or an existing projects/<slug>/script.json — research + script, find footage, review it with vision (parallel reviewer sub-agents), fetch, render the collage edit, publish to the Studio. Use when asked to make, produce, run or re-run a documentary/video/production.
---

# /documentary — topic → finished collage documentary

Everything runs in this repo with free/open-source tools; the only paid dependency is the Claude
subscription running this session. Paths below are relative to the repo root. Keep the user
posted with one short line per stage; they follow along in the Studio (icedlatte.app / `editor/`).

## 0. Preconditions (check once, fail fast)
- `python -m footage.find --list-hosts` → every host it lists must be reachable
  (`curl -s -o /dev/null -w '%{http_code}' https://commons.wikimedia.org/w/api.php` ≠ 000).
  If blocked: say which hosts, point the user to the environment's Network access setting, and
  continue with the stages that don't need them (script, render with fallbacks).
- `ffmpeg`, Kokoro model files (`KOKORO_DIR`), ISNet model (auto-downloads from GitHub).

## 1. Script (skip if `projects/<slug>/script.json` exists)
Spawn ONE research+script sub-agent with the brief used for `projects/new-coke/` (see its
`research.md` + `script.json`): sourced facts only, 7–9 min for tests / 40–60 min for full
episodes (then split research across parallel sub-agents per era/chapter), every beat with a
`scene` (collage renderer contract, `collage/README.md`) and a `request` (what must be on screen,
per-archive queries, `fallback`). Output: `research.md`, `script.json`, `requests.json`.

## 2. Find candidates (machine)
```bash
python -m footage.find projects/<slug>/requests.json --out projects/<slug>/footage
```
Produces one contact sheet per request (`footage/contact/<id>.jpg`), zooms, `candidates.json`,
`picks.template.json`.

## 3. Review with eyes (parallel sub-agents) — the step that stops random footage
Split the requests into batches of ~8–12 consecutive ids (keep a chapter together) and spawn one
reviewer sub-agent per batch **in parallel** (single message, several Agent calls). Each gets:
- the batch ids, the script beats for those ids (text + scene + request), and
- the instruction to follow `footage/REVIEW.md` exactly: open each `contact/<id>.jpg` with Read,
  open zooms for close calls/faces/videos, write picks for its ids to
  `projects/<slug>/footage/picks.<batch>.json` (pick / alt / crop / in / dur / notes / requery).
  Reviewers must reject anything that doesn't visibly show the subject — "nothing good" plus
  better `requery` queries beats a wrong picture.
When all return: merge into `footage/picks.json`. For ids with `pick: null`, run
`python -m footage.find footage/requery.json --out projects/<slug>/footage` (after fetch writes
it) or build a requery file from the reviewers' `requery` fields, and review those again (max 2
rounds; then the beat uses its scripted fallback).

## 4. Fetch
```bash
python -m footage.fetch projects/<slug>/footage/picks.json \
  --candidates projects/<slug>/footage/candidates.json --out projects/<slug>/footage/media
```
Writes `media/<beat id>.<ext>` + `sources.json` (licence ledger → video description credits).

## 5. Produce + publish
```bash
python -m pipeline.produce projects/<slug> --media projects/<slug>/footage/media \
  --out out/<slug> --feed editor/apps/web/public/studio-feed
```
Narration → one collage scene per beat cut to the voice → final mix → Studio feed (live stage
updates). Long episodes: render on vast.ai instead (`docs/04-burst-render-vast.md`).

## 6. Watch it before reporting (eyes again)
Pull one frame per beat (`ffmpeg ... select` at each beat midpoint from `out/<slug>/report.json`),
tile into sheets, look at them. Fix and re-render only broken beats (`collage/`: re-run
`render_scene` for that beat's `out/<slug>/scenes/<id>.json`, then re-assemble). Report: runtime,
beats with real footage vs fallback vs "needs footage", licence summary, anything to decide.

## Rules
- Never fill a gap with unrelated footage. Missing = scripted fallback or a visible NEEDS FOOTAGE card.
- Never present paraphrased headlines as real newspapers (produce.py converts them to title cards).
- NC/ND licences never; YouTube only when explicitly allowed, ≤ 8 s, commented on.
