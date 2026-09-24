# Editing and running the pipeline in the cloud (no GPU, Claude subscription only)

Decisions in this doc:

1. The edit is built in **a real, free video editor: Shotcut**, using its MLT engine.
2. The whole pipeline runs **inside Claude Code cloud sessions**, so the user needs no machine or GPU.
3. Everything must run on **CPU only**. The numbers below were **measured** in a Claude Code
   cloud container, not estimated.

---

## 1. Why Shotcut (MLT) is the editor

We want an actual editor: a timeline a human can open and fix, a consistent look, and effects and
keyframed animation. We also want a machine to build and render it headlessly.

| Editor | Free | Scriptable headless | CPU-only render | Verdict |
|---|---|---|---|---|
| **Shotcut** | GPL | **Project file *is* MLT XML.** The `melt` command-line tool renders it directly, and Shotcut opens the same file. | Yes | **Chosen** |
| Kdenlive | GPL | Also MLT, but the `.kdenlive` file carries a lot of app-specific metadata that's easy to get wrong when generating it | Yes | Good second choice |
| DaVinci Resolve (free) | Freeware | Scripting is limited in the free version, and there's no headless render | **Needs a GPU** | Ruled out |
| Blender VSE | GPL | Python, headless | Yes, but slow for long timelines | Backup for fancy titles only |
| CapCut | Freemium | No API; terms, watermark and paywall issues | — | Ruled out |

**How it works:** Claude never "drives the mouse". The pipeline produces an edit decision list
(`edl.json`), and a generator writes it out as a **Shotcut project (`.mlt`)** with named tracks:

```
V4  captions / labels      (Shotcut Text filters: lower thirds, name/date/figure labels)
V3  graphics               (maps, timelines, figures, document cards: rendered MOVs with alpha)
V2  house overlay          (vignette + grain plate + frame/letterbox: identical on every video)
V1  footage & photos       (clips with Ken Burns keyframes, transitions between them)
A1  narration              A2  music bed (ducked)      A3  SFX (whooshes, hits, risers)
```

- **Rendering:** `melt project.mlt -consumer avformat:final.mp4 …` runs headlessly.
- **Human editing:** the *same* file opens in Shotcut if you want to nudge a cut, swap a shot or
  fix a label. Then re-render.

## 2. Consistent style: the "house pack"

Everything that makes the channel recognisable is fixed data in `house/`, applied the same way to
every video:

| Element | Implementation |
|---|---|
| Colour grade | One **3D LUT** (`house/look.cube`) applied as a single filter on the footage track (MLT `avfilter.lut3d`). This is the cheapest way to get a filmic grade on CPU. |
| Vignette, grain, texture | A pre-rendered overlay plate on V2, the same on every video |
| Motion | Ken Burns presets (slow push, drift left or right, reveal) as keyframed position and size. Every still moves, and archival clips get a subtle drift. |
| Transitions | A fixed palette: hard cut (default), 12-frame dissolve at scene changes, dip-to-black at chapters, whip + whoosh at hook beats |
| Typography | Shotcut Text filters with the house fonts (OFL-licensed), in fixed positions and sizes for lower thirds, dates and figures |
| Graphics | Templates (map, timeline, figure, document, person card, chapter card) rendered with the house palette and exported as alpha MOVs |
| Sound | Music bed chosen per chapter from the house library, sidechain-ducked under the voice. SFX are synthesised. Mastered to −14 LUFS. |

Claude only picks which preset goes where. It never invents a new look, so every video matches.

## 3. Monetisation: what the edits do and don't do

These are real facts about YouTube, and the design depends on them:

- **Filters, crops, LUTs and zooms do *not* avoid Content ID.** Its matching survives colour grading
  and reframing. If the footage is someone else's copyrighted clip, the video can still be claimed
  however heavily it's styled.
- **What gets a channel monetised** under YouTube's reused-content policy is **original
  commentary and narrative**: our researched script and narration, plus editing that adds meaning
  (maps, timelines, labels, argument structure). The edit style supports this, but it isn't a
  licence.
- So the house rules are:
  1. **Default footage is public domain, CC or free-licence** (Wikimedia Commons, Internet
     Archive, NASA, US National Archives, Library of Congress, Pexels, Pixabay). A licence ledger
     builds the credits in the description automatically.
  2. **Every minute is transformed**: continuous original narration, graphics about once a
     minute, labels on names and numbers, and no stretch of source footage longer than about 8 s
     without an edit or overlay.
  3. Copyrighted clips (YouTube, news) are **opt-in, short (≤ 5–8 s), and only where the narration
     is commenting on that clip**, and they're flagged in the QC report.

## 4. CPU-only: measured benchmarks

Container: Claude Code cloud session, **4 vCPU (Xeon 2.8 GHz), 15 GB RAM, no GPU**.
Output 1080p30 H.264 (x264 `superfast`, CRF 20).

| Test | Result |
|---|---|
| Styled edit in MLT with slow filters (affine zoom, eq, colour balance, vignette, temporal noise) | 58 s of video in **216 s**, about 3.7× slower than real time. **Too slow.** |
| House look as **LUT + overlay plate + light grain**, one render | 20 s in ~26 s |
| Same look, **4 chapter renders in parallel** | **80 s of video in 78 s, about 1× real time** |
| **Kokoro narration** (82M, ONNX, CPU) | **2.4–2.7× faster than real time**, so 60 min of voice in ~24 min |

What this means:

| Video length | Voice | Render | Wall clock in one cloud session* |
|---|---|---|---|
| 10 min | ~4 min | ~10 min | **~15–20 min** |
| 60 min | ~24 min | ~60 min | **~1.5–2 h** |

\*Research, script and footage selection run while earlier chapters are being voiced and
rendered, so they add little on top.

**Honest conclusion.** Without a GPU, an hour-long documentary **can't** go from topic to
render in 10 minutes. What *does* hold:

- **Human time stays around 10 minutes**: pick the angle, approve the outline, watch the result.
- **It runs unattended in the cloud**, so it doesn't matter that the render takes 1.5–2 h.

Ways to speed it up later:

- Fan chapters out to **parallel cloud sessions**, one chapter each, then concatenate. That's
  roughly 5–6× faster, but chunk hand-off needs storage.
- Render the draft at 720p for review and the final at 1080p.
- A user's own 8–12-core laptop is about 2–3× faster than this container.

## 5. Voice without a GPU

- **Kokoro-82M (Apache-2.0)** is the only strong narrator we've measured that runs faster than
  real time on CPU. It's the default.
- Chatterbox and other expressive, voice-cloning models are **too slow on CPU** for hour-long
  audio, so they're out under this constraint.
- The "compelling" part therefore comes from direction and post-production:
  - a script written for the ear;
  - sentence-by-sentence synthesis with scripted pauses;
  - `speed` varied per passage (slower for reveals, faster for chases);
  - mastering: high-pass, compression and loudness normalisation;
  - music and SFX to carry emotion.
- Timing: each sentence is synthesised separately, so sentence boundaries are exact. Word-level
  times for labels come from Kokoro's phoneme durations, or from whisper `tiny` on CPU if needed.

Listen to the samples (British `bm_george`, American `am_michael`) to judge whether this meets the
bar. If it doesn't, the only $0 upgrade path is a GPU somewhere, which breaks the no-GPU constraint.

## 6. Footage selection: subscription only

Jev is a **separate paid API** (tiny cost, but not part of the Claude subscription). Under the
strict "Claude subscription only" rule it becomes **optional**, and the default is:

1. Claude writes source-specific searches per ~20–30 s of narration.
2. Free archive APIs return candidates. We fetch low-resolution previews and extract keyframes at scene cuts.
3. **CLIP ViT-B/32 on CPU** (about 20–50 frames/s on 4 cores) ranks keyframes against each
   sentence and drops blurry, low-res, watermarked or off-topic frames.
4. **Claude vision** picks from a 4×4 contact sheet of the top candidates per slot. That's about
   150 sheets per hour of video, one image each, inside the subscription.
5. Only the chosen seconds are downloaded at full resolution.

Jev can be switched on later as a speed and quota saver for step 4, if a few cents per video is acceptable.

## 7. Running it "outsourced" in Claude Code cloud sessions

| Need | How |
|---|---|
| Start a video | Open a Claude Code session on this repo and run `/documentary "<topic>" --minutes 60` (from web or phone). |
| Tools | `melt` (Shotcut's engine), `ffmpeg` and `frei0r` come from the Ubuntu mirror. Kokoro and CLIP come from pip plus model files. An environment **setup script** installs and caches them. |
| Network | **Current environment blocks every footage source** (tested: `commons.wikimedia.org`, `archive.org`, `images-api.nasa.gov`, `catalog.archives.gov`, `loc.gov`, `pexels.com`, `pixabay.com`, `youtube.com`, `huggingface.co`, `dvidshub.net` were all denied). The environment's **Network access** needs these domains allowed (cloud environment menu → Edit). |
| Delivery | A 1-hour 1080p file is 2–4 GB, too big for git. The session **uploads it straight to YouTube as Private/Unlisted** through the YouTube Data API (OAuth token stored as an environment secret, `googleapis.com` allowed). The default API quota is enough for about 6 uploads a day. You review and publish. |
| Resilience | Every stage checkpoints to `projects/<slug>/`. Renders are per chapter, so an interrupted session resumes where it stopped. |
| Disk | ~30 GB free per session, which is enough for one hour-long project (previews, not full source files). Scratch files are cleaned per chapter. |

## Appendix: benchmark commands

```bash
# house look, one chapter chunk (run 4 in parallel on 4 vCPU)
melt -profile atsc_1080p_30 chunk.mlt \
  -filter avfilter.lut3d av.file=house/look.cube av.interp=nearest \
  -filter avfilter.noise av.alls=6 av.allf=u \
  -track house/vignette.png -transition composite a_track=0 b_track=1 \
  -consumer avformat:out.mp4 vcodec=libx264 preset=superfast crf=20 threads=1

# Kokoro on CPU
python -c "from kokoro_onnx import Kokoro; k=Kokoro('kokoro-v1.0.onnx','voices-v1.0.bin'); k.create(text, voice='bm_george', speed=0.95, lang='en-us')"
```
