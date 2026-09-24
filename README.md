# Documentary

A long-form YouTube documentary studio driven by Claude. You give it a topic and it returns a
finished, narrated documentary with a thumbnail and description. The only paid dependency is your
Claude subscription. It runs CPU-only in Claude Code cloud sessions, with no GPU and no other paid services.

- [Competitor analysis: Frontier](docs/01-competitor-analysis-frontier.md): what it does, what it costs, where it's weak
- [Editing & cloud pipeline](docs/03-editing-and-cloud-pipeline.md): Shotcut/MLT editor, house style, CPU-only benchmarks, running in Claude Code cloud sessions
- [Burst rendering on vast.ai](docs/04-burst-render-vast.md): rent the fastest GPUs per second, execute a prepared job, destroy them
- [Studio: the interface](docs/05-studio-ui.md): our OpenReel-based editor with the production hub
- [Product blueprint](docs/02-product-blueprint.md): pipeline, voice, footage, graphics, cost and time budgets, roadmap

## Studio (the interface)

```bash
cd editor && pnpm install && pnpm dev    # http://localhost:5173
```

Our fork of the OpenReel editor (MIT, see `editor/FORK.md`) with a Studio home screen: productions,
live pipeline stages, script, footage and licences, renders, and "Open in editor".

## Burst renderer (working code)

```bash
pip install -r requirements-launcher.txt -r requirements-worker.txt   # + melt, ffmpeg
python examples/make_demo.py projects/demo          # synthetic test job
python -m worker.worker --local projects/demo --out out/demo   # render it on this machine
python -m burst.launch projects/demo --shards 2 --dry-run      # show the vast.ai machines it would rent
python -m burst.launch projects/demo --shards 2                # rent → render → destroy
python -m pytest -q tests
```

Status: the burst renderer works end to end locally. The first real vast.ai run needs the API key,
the R2 bucket and network access (see docs/04).
