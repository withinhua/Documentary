# Burst rendering on vast.ai: rent, execute, destroy

**Principle:** GPU time is billed per second, so the GPU must only ever *execute*. All thinking,
research, footage finding and downloading happens beforehand at no GPU cost. The instance gets one
prepared bundle, does the heavy work, uploads the result and is destroyed.

```
 Claude Code cloud session (subscription, CPU)                Object storage (Cloudflare R2)        vast.ai GPU machines (per-second)
 ─────────────────────────────────────────────                ─────────────────────────────         ─────────────────────────────────
 research → script → edit plan (sentence-anchored)
 find + pick footage → download only the chosen seconds
 bundle.build_shards() → one .tar per chapter shard  ───────►  runs/<run>/shard-00..N.tar
 burst.launch: rent N machines at once ────────────────────────────────────────────────────────────►  boot (image pull: NOT GPU-billed)
                                                               shard tar  ─── 16 parallel streams ─►  download + verify checksums
                                                                                                      voice (Kokoro on CUDA)
                                                                                                      build Shotcut/MLT project
                                                                                                      render: parallel slices, NVENC
                                                                                                      mix audio, −14 LUFS, mux
                                                               runs/<run>/out/shard-k.mp4 ◄───────  upload → self-destruct
                                                               runs/<run>/out/final.mp4   ◄───────  leader joins shards → upload → self-destruct
 launcher destroys all instances, verifies none are left, prints the cost
```

## What's built (in this repo)

| File | Does |
|---|---|
| `burst/bundle.py` | Splits a prepared job into shards of whole chapters balanced by word count, and packs one tar per shard with only that shard's media and a SHA-256 manifest |
| `burst/storage.py` | S3-compatible storage (R2): parallel multipart upload and **presigned URLs**. The rented machine never sees storage credentials. |
| `burst/vast.py` | vast.ai REST client: search offers, rank fastest-first, pick N distinct machines with the same GPU, create, show, destroy |
| `burst/launch.py` | Orchestrator: upload → rent N in parallel → monitor → retry a dead machine on a spare → **destroy everything in `finally`** (success, error, Ctrl-C or deadline) → sweep by label → cost report |
| `burst/reap.py` | Safety net: destroys any `docburst-*` instance older than X minutes (run on a schedule) |
| `worker/worker.py` | On the GPU: parallel ranged download → verify → narrate → build edit → render → upload → (leader) join shards |
| `worker/tts.py` | Kokoro-82M on ONNX Runtime, using CUDA when available, with per-beat pacing and pauses and exact beat timings |
| `worker/mlt.py` | Writes the edit as an **MLT project (the format Shotcut opens)**: house LUT, grain, Ken Burns presets, labels, vignette plate, narration track |
| `worker/render.py` | Renders timeline slices in parallel with `melt`, uses NVENC when present (up to 8 sessions), joins losslessly, ducks music, masters loudness |
| `worker/run.sh` | Entry point: a deadline watchdog self-destructs even if the job hangs, logs are uploaded, then self-destruct |
| `worker/Dockerfile` + `.github/workflows/worker-image.yml` | The worker image, built in GitHub Actions and published to GHCR, so nobody needs Docker locally |
| `house/make_house.py` | The one house look (LUT + vignette), identical on every video |

**Verified here:**

- The worker runs end to end on CPU on a demo job (voice → Shotcut project → render → mix). The
  output is 1080p H.264 + AAC at −14.4 LUFS, and the frames show the grade, vignette, photo
  push-in, name label and clip drift.
- A 4-slice parallel render is frame-exact (1,228 of 1,228 frames).
- 6 tests pass, including "every instance is destroyed on success **and** on failure".

**Not verified yet:** a real vast.ai run. This environment's network policy blocks
`console.vast.ai` and R2, and there's no API key yet.

## Three layers of "the instance is dead"

1. **The worker destroys itself** when it finishes, using the `CONTAINER_ID` and
   `CONTAINER_API_KEY` that vast.ai injects.
2. **A watchdog** in `run.sh` destroys the instance at the hard deadline, even if rendering hangs.
3. **The launcher** destroys every instance it created in a `finally` block, sweeps anything with
   the run's label, and `burst.reap` catches anything left behind if the launcher itself dies.

A destroyed instance stops all billing, storage included. A merely *stopped* one keeps charging
for disk, so we never stop, we always destroy.

## Why these choices make it fast

| Choice | Effect |
|---|---|
| **Fan out across N machines** | Per-second billing makes 6 machines × 2 min cost about the same as 1 machine × 12 min, but it finishes 6× sooner |
| **RTX 5090 / 4090 / L40S, not H100/A100** | Datacenter GPUs have **no NVENC** video encoders. Consumer and pro cards do, and they're cheaper per hour. |
| **Filter: ≥ 32 CPU cores, ≥ 2 Gbps down, reliability ≥ 98 %, verified** | The look filters use the CPU cores, and the bundle arrives in seconds |
| **Only chosen seconds are shipped** | A 60-min video needs roughly 5–10 GB of trimmed clips, not hundreds of GB of source video |
| **Uncompressed tar + 16 parallel range requests** | Saturates the machine's link, and extraction runs at disk speed |
| **Everything baked into the image** (models, fonts, LUT, tools) | No installs or model downloads after boot |
| **Image pull happens in `loading`** | vast.ai starts GPU billing at `running`, so boot time costs only storage (fractions of a cent) |
| **The Claude subscription does all thinking beforehand** | No LLM, no search and no decisions on the clock |

## Time and cost: expected, to be measured on the first real run

60-minute documentary, 6 shards (about 10 min each), RTX 5090-class, 32–64 cores, ~$0.50–0.80/h:

| Step (per machine) | Estimate | Basis |
|---|---|---|
| Rent → running (image pull) | 0.5–3 min | Depends on host cache and bandwidth. **Not GPU-billed.** |
| Download shard (~1–1.5 GB) | 3–8 s | 2–5 Gbps, parallel streams |
| Voice (10 min of audio) | ~5–15 s | Measured 2.5× real time on 4 CPU cores; a GPU is much faster |
| Render 18,000 frames | ~45–90 s | Measured ~1× real time per 4 cores, scaled to 32–64 cores with NVENC |
| Mix + upload | ~10–20 s | |
| **GPU-billed time per machine** | **~1.5–2.5 min** | |
| Leader: join 6 shards + upload final | +30–90 s | Downloads peers and joins without re-encoding |
| **Wall clock, launch → final.mp4** | **~3–6 min** | Mostly boot |
| **GPU cost** | **~$0.10–0.20 per hour-long video** | 6 × ~2 min × ~$0.7/h |

**On "one minute or quicker":**

- **GPU-billed time per machine of about 1–2 minutes is realistic.** Add shards to shrink it further.
- **The whole cycle including boot is not.** How fast a host pulls the image is outside our
  control, and it typically adds 30 s to 3 min. Boot isn't GPU-billed, so it costs time, not money.

## Setup (one time)

1. **vast.ai:** create an account, add credits, and create an API key.
2. **Cloudflare R2:** create a bucket `docburst` and an S3 API token. Add a lifecycle rule
   deleting `runs/` after 7 days. That keeps you inside the 10 GB free tier, and egress is free.
   (GitHub is the wrong place for footage: 100 MB per-file limit, and LFS bandwidth is billed.)
3. **Worker image:** merge to `main` and the `worker-image` workflow publishes
   `ghcr.io/<owner>/docburst-worker`. Make the package public, or give vast.ai registry credentials.
4. **Claude Code cloud environment:** add `VAST_API_KEY`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`,
   `S3_SECRET_ACCESS_KEY`, `S3_BUCKET` and `BURST_IMAGE` as environment secrets. Allow
   `console.vast.ai`, `<account>.r2.cloudflarestorage.com` and the footage sources in Network
   access.
5. Run it:

   ```bash
   python -m burst.launch projects/<slug> --shards 6 --dry-run   # shows the machines it would rent
   python -m burst.launch projects/<slug> --shards 6             # rent → render → destroy
   ```

## Next

- **First real run:** measure boot, render and upload per step, and tune slices and shards.
- **Move the look to the GPU** (ffmpeg `libplacebo` / CUDA filters) so the render stops being
  CPU-bound. This is the biggest remaining speed-up.
- **Multipart presigned upload** for the leader's final file (> 5 GB, and faster in parallel).
- **Expressive GPU voice** (Chatterbox, MIT) now that there is a GPU: A/B it against Kokoro on the first run.
- **Graphics templates** (maps, timelines, figures) rendered on the same machines.
- **Prep stage** in Claude Code: research → script → edit plan → footage download, writing the
  `job.json` format that `examples/make_demo.py` shows.
