# Our fork of OpenReel

This editor is based on [OpenReel Video](https://github.com/Augani/openreel-video) (MIT, © 2024-2026
Augustus Otu and Contributors), upstream commit `5f3c85e5fc223c86060bf4b12e1b4dec58e9b8a9`. The MIT licence in
`LICENSE` must ship with the product. Everything else is ours to change and sell.

## What we changed

| Area | Change |
|---|---|
| **Studio** (`apps/web/src/studio/`) | New home screen: productions, live pipeline stages, script, footage + licences, narration, renders, activity |
| **Open in editor** (`studio/build-timeline.ts`) | Builds a real timeline from a production through OpenReel's own agent tools (tracks, trims, house-look effects, Ken Burns keyframes, labels, narration, chapter markers) |
| Routing | `#/studio` is the landing page (`#/studio/<slug>` deep-links a production); the editor's home button returns to it |
| Branding | Product name lives in `studio/brand.ts`; visible "OpenReel" strings replaced |
| **Bug fix** (`packages/core/src/actions/action-executor.ts`) | `clip/trim` with both in and out points computed the duration from the old in-point |
| Removed | Upstream's unused 139 MB `icons/` folder, screenshots and tooling notes |

## Pulling upstream improvements

```bash
git clone https://github.com/Augani/openreel-video /tmp/upstream
cd /tmp/upstream && git diff 5f3c85e5fc223c86060bf4b12e1b4dec58e9b8a9 HEAD > /tmp/upstream.patch
cd <this repo>/editor && git apply --3way /tmp/upstream.patch   # resolve conflicts in our changed files
```
