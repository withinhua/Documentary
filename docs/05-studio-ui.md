# Studio: the interface

The Studio is our product's home screen, built into our fork of the OpenReel editor (`editor/`).
It is where you follow every production while the pipeline works, and where you edit the result.

```
 pipeline (Claude + worker)                 Studio feed (static files)              Studio UI (browser)
 ─────────────────────────                  ──────────────────────────              ───────────────────
 status.json per stage  ──────────────────► <feed>/<slug>/production.json  ──poll─► stage tracker, activity
 job.json (script, beats, licences)         script + beat timings                    Script tab (click → seek)
 media (full-res, stays for render)  ─────► 720p proxies + thumbnails        ──────► Footage tab (licence per item)
 narration.wav                       ─────► narration.mp3                    ──────► Watch tab
 renders (worker / vast.ai)          ─────► render proxies + report          ──────► player + per-step timing
                                                                                     "Open in editor" → real timeline
```

## Run it

```bash
cd editor && pnpm install && pnpm dev          # http://localhost:5173 → opens the Studio
# in another terminal, from the repo root: make a demo production and watch it appear
python examples/make_demo.py projects/demo
python -m worker.worker --local projects/demo --out out/demo --feed editor/apps/web/public/studio-feed
```

The feed republishes on every stage change, so the stage tracker moves while the worker runs.
`VITE_STUDIO_FEED` points the UI at a different feed (for example an R2 bucket with public read)
when we host it.

## What's on screen

- **Productions list:** thumbnail, length, state (draft / running / ready / done / failed), progress.
- **Stage tracker:** Research → Script → Voice → Footage → Edit → Render → Publish, each with its
  status, what it did, and how long it took.
- **Watch:** the latest render with the script line under the picture as it plays, plus render
  stats (encoder, speed, time per step) and the render project file.
- **Script:** chapters and beats with timecodes, the shot on each beat, labels, motion and pace.
  Click a beat to jump the player there.
- **Footage:** every clip and photo with source, licence, credit, link and how often it's used.
  Missing licences show in red.
- **Activity:** the pipeline's log.
- **Open in editor:** builds the production as an editable timeline (Labels · Footage · Narration
  tracks, house-look effects on every shot, Ken Burns keyframes on photos, chapter markers), using
  the same agent tools Claude uses, so the result is a normal project you can change by hand.

## Next

- A **"New production"** form: topic → the pipeline starts, and the production appears here immediately.
- **Hosting:** deploy the editor and serve the feed from R2, so the Studio works from any device.
- **Live agent control:** a WebSocket MCP bridge so a Claude session can edit the open timeline
  while you watch (the desktop build already has an MCP server we can reuse).
- **Templates:** our graphics (maps, timelines, figures, document cards) as editor templates.

## Hosting

The Studio deploys to Vercel as project `documentary-studio` (root directory `editor/`, settings in
`editor/vercel.json`). Vercel builds on every push. The build is static (`pnpm build` →
`apps/web/dist`), served with the cross-origin isolation headers the editor's multithreaded video
engine needs. The hosted Studio shows the committed compact demo production until the feed moves
to R2 (`VITE_STUDIO_FEED`).
