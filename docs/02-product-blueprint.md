# Product blueprint: topic → long-form YouTube documentary

> **Update:** the editing, no-GPU and cloud-execution decisions in
> [03-editing-and-cloud-pipeline.md](03-editing-and-cloud-pipeline.md) (with measured benchmarks)
> **override** the GPU-based parts of this doc. Specifically: Kokoro on CPU replaces Chatterbox,
> Shotcut/MLT replaces the custom assembler, CLIP + Claude vision replace Jev by default, and the
> time budget in §8 assumes a GPU that we no longer use.

**Goal:** you type a topic and get a finished, monetisation-safe, high-retention documentary
(10–90 min) with a compelling narrated voiceover, real footage, animated graphics, sound design,
captions, a thumbnail and a description.

**Hard constraints**

| Constraint | Target |
|---|---|
| Cash cost per 60-min video | **~$0.** Nothing but the Claude subscription. |
| External paid services | **None.** Only the user's Claude subscription (Pro/Max). Jev is optional and off by default. |
| Hardware | **None of the user's own, and no GPU.** It runs CPU-only inside Claude Code cloud sessions. |
| Editor | **Shotcut (MLT).** Claude generates the project, `melt` renders it headlessly, and a human can open the same file. |
| Style | **One consistent house documentary style.** No per-video style switching. |
| Human time per video | **~10 min** (pick a title/angle, approve the outline, review the final video) |
| Wall-clock time (measured, 4 vCPU) | 10-min video in ~15–20 min; 60-min video in ~1.5–2 h, unattended |

---

## 1. The honest feasibility check

These are the four places where a naive build would miss the targets, and how we solve each one.

1. **Voice is the only real cost in competitors, and we make it local.** ElevenLabs-class voices
   cost ~$10–30 per hour of audio. Open-weights TTS is now competitive for narration, so we run it
   locally ($0) and, if needed, on a rented GPU. A 4090-class GPU rents for well under $1/hr, and one
   run needs a fraction of an hour. **That rented GPU is the only thing the $1 budget is for.**
   - Licences matter because we monetise. Default engines are **Chatterbox (MIT)**, which is
     expressive and clones a voice from a short sample, and **Kokoro-82M (Apache-2.0)**, which is
     fast and runs on CPU. **Avoid** XTTS-v2 (CPML, non-commercial) and Fish-Speech (CC-BY-NC).
     Re-check the licence of any newer model (Hume TADA, VibeVoice, etc.) before adding it.
   - "Extremely compelling" narration comes from **direction** as much as from the model. See §4.

2. **Footage without paying and without Content ID claims.** Default sources are free APIs with
   licences that allow reuse: Wikimedia Commons, Internet Archive (Prelinger and public-domain
   films), NASA Image & Video Library, US National Archives (NARA), Library of Congress, DVIDS (US
   military, public domain), Smithsonian Open Access, Europeana, Pexels and Pixabay. Every asset goes
   into a **licence ledger**, and the credits are written into the description automatically.
   YouTube excerpts are an **opt-in** "fair-use commentary" mode, used for short clips and with a
   visible warning.
   - The catch: recent events (roughly post-1970, and especially post-2000) have thin
     public-domain coverage. We fill the gap with **real photos plus motion** (parallax, spotlight,
     Ken Burns), **maps, document cards, timelines and data graphics**. That is the same toolkit
     Vox, Johnny Harris and Magnates Media use.

3. **Shot selection at long-form scale.** An hour needs ~400–700 shots picked from thousands of
   candidates. Claude vision alone would burn the subscription, so we use three tiers (see §3a):
   local CLIP for a visual prefilter, **Jev** for fast and cheap semantic scoring of every candidate
   against every sentence, and Claude vision only for the final shortlist and for the hook.

4. **Claude subscription usage is the real rate limit, not dollars.** An hour-long documentary is
   about 9,000 narrated words, plus research, a critique pass and ~100–200 contact sheets. Rough
   estimate: **0.5–1.5 M tokens per hour of video.** A Max plan should handle several long videos a
   day. Pro will run into limits. **Phase 0 measures this precisely.** Design rules:
   - Deterministic work (timing, rendering, audio, file handling) never goes through Claude.
   - Claude returns **compact JSON specs**, never per-video code. Graphics are templates that take
     parameters.
   - CLIP and Jev filter candidates first, so Claude only looks at the best 2–3 per slot.

---

## 2. How Claude is driven (subscription-only)

We use two cooperating modes, both of which run on the user's Claude login:

- **Interactive:** open the repo in Claude Code and run `/documentary "The Fall of Enron"`.
  `CLAUDE.md` and a project skill make Claude the showrunner. It runs research subagents in
  parallel, asks the user to pick an angle, and calls the Python pipeline for the heavy work.
- **Headless:** the Python orchestrator calls `claude -p --output-format json` (Claude Code
  print mode, using the logged-in subscription) for each LLM step, with a strict JSON schema per
  step. This makes the pipeline resumable, parallel (one call per chapter) and testable.

We don't need an `ANTHROPIC_API_KEY`. If a user sets one, it's an optional backend.

---

## 3. Pipeline

Every stage writes its artifact to `projects/<slug>/`, so any stage can be re-run or edited by hand.

```
topic ─► 0 Packaging ─► 1 Research ─► 2 Outline ─► 3 Script ─┬─► 4 Voice ──────────┐
                                                             ├─► 5 Director ─► 6 Assets ─► 7 Graphics ─┤
                                                             └──────────────────────────────────────── 8 Assembly ─► 9 QC ─► 10 Publish kit
```

Stages 4–7 run **per chapter as soon as that chapter's script is final**. Chapter 1 is being voiced
while chapter 5 is still being written.

| # | Stage | Engine | Artifact |
|---|---|---|---|
| 0 | **Packaging first.** Generate 10 title and thumbnail concepts and score them for curiosity gap, specificity and searchability. The user picks one, or we auto-pick. The title's promise drives everything after it. | Claude | `package.json` |
| 1 | **Research.** Parallel subagents cover the timeline, key people, numbers, contradictions, "things most people get wrong" and primary sources, using WebSearch/WebFetch, the Wikipedia and Wikidata APIs, and archive.org texts. Every fact carries a source URL. | Claude + free APIs | `facts.jsonl`, `timeline.json`, `entities.json` (people/places/dates/figures) |
| 2 | **Outline.** Long-form structure: cold open (0–60 s) → promise → 6–10 chapters, each with its own question, escalation and payoff that opens the next question. Midpoint reversal, climax, resolution and an end-screen hook. The user approves it (~1 min). | Claude | `outline.json` |
| 3 | **Script.** Chapters are written in parallel from a shared *style bible* and the outline. Then a **retention critic** (hook strength, open loops, a re-hook every 2–4 min, no filler, concrete specifics) and a **fact-checker** (every claim traced to `facts.jsonl`) run, then a rewrite. The output includes **performance markup** (see §4). | Claude | `script.md`, `script.json` |
| 4 | **Voice.** TTS runs per paragraph. faster-whisper checks every chunk (word-error rate, clipping, dropped words), and bad chunks are retaken automatically. Then the mastering chain runs. The result includes word-level timestamps. | Local TTS + faster-whisper | `narration.wav`, `words.json`, `captions.srt` |
| 5 | **Director.** For each sentence, decide the visual beat: `footage`, `photo`, `map`, `timeline`, `figure`, `quote/document`, `person card`, `chapter card`, or `collage`. It follows density rules (dense hook, then about one graphic a minute plus a label for every name and number) and variety rules (no layout repeated within 90 s). | Claude | `edl.json` (edit decision list) |
| 6 | **Assets.** Query the providers → fetch low-res previews → keyframes → local captions → CLIP prefilter → **Jev scoring** → Claude contact-sheet pick (hook + ties only) → download full-res of chosen seconds → trim → licence ledger. Photos get cutouts (rembg) and depth maps (Depth-Anything-V2) for parallax. | Free APIs + local models + Jev + Claude vision | `assets/`, `licenses.json` |
| 7 | **Graphics.** Render template scenes from the EDL parameters in parallel. | HTML/GSAP templates → headless Chromium frames, plus ffmpeg | `gfx/*.mov` |
| 8 | **Assembly.** Render the timeline in parallel segments with a hardware encoder (NVENC/VideoToolbox), then concat. Add the music bed with sidechain ducking, synthesised SFX on cuts and reveals, mastering to −14 LUFS, and optional burnt captions. | ffmpeg | `final.mp4` |
| 9 | **QC.** Automatic checks: black or frozen frames, repeated shots, silence gaps, loudness, caption sync and watermark detection. Claude reviews a contact sheet of the finished video (1 frame / 10 s), the hook and chapter openings, and patches the EDL. Only the affected segments are re-rendered. | ffmpeg + Claude vision | `qc.json` |
| 10 | **Publish kit.** Three thumbnail variants (a real-photo cutout plus bold type on the style template), which Claude compares by vision. Also a description with chapters, sources, licence credits and tags. | Claude + templates | `thumb_*.jpg`, `description.md` |

### 3a. Footage engine: where Jev fits

**Jev** (TypeSafe AI, launched Sept 2026) is a text-only "System One" classification model. You
send it text plus typed questions (a choice, a 0–1 score, or yes/no). It returns probabilities in
~70–500 ms, and pricing is ~$0.042 per million input tokens with free output. **It doesn't search for
or download footage, and it can't see pixels.** So it is our *judge*, not our *finder*.

| Step | Tool | Why |
|---|---|---|
| Find candidates | Archive APIs (Commons, Internet Archive, NASA, NARA, LoC, DVIDS, Pexels, Pixabay) + optional YouTube search; queries written by Claude per ~20–30 s of narration | Jev can't search |
| Turn shots into text | ffmpeg scene-cut keyframes → local captioner (e.g. Florence-2, MIT) plus source metadata, date, uploader, and transcript lines around the timestamp | Jev only reads text |
| Visual prefilter | Local SigLIP/CLIP: drop clearly irrelevant, blurry or low-res frames | Free, catches what text misses |
| **Semantic scoring** | **Jev**, one call per (sentence, candidate): `relevance` score, `era_matches` yes/no, `shows_named_subject` yes/no, `has_watermark_or_overlay_text` yes/no, `shot_type` choice | Thousands of decisions in seconds, run in parallel, costs cents |
| Final pick | Claude vision on 4×4 contact sheets, **only** for the hook, chapter openers and close calls | Keeps subscription usage low where it matters least |
| Variety and pacing | Deterministic rules: no source reused within N minutes, and alternate shot types | No model needed |

**Cost sanity check (60-min video):** ~150 footage slots × ~40 candidates × ~400 tokens is about
2.4M tokens, or **~$0.10**. Even at 5× that it stays well inside the $1 budget.

**Caveats to verify in Phase 0:** Jev is days old. Test its accuracy against Claude's picks on the
same contact sheets, check its rate limits and uptime, and keep a fallback. The fallback is CLIP
plus Claude, which is slower and uses more of the subscription but still works.

---

## 4. What makes the narration compelling (free models, directed well)

1. **Write for the ear.** The script prompt enforces short declarative lines at peaks, longer
   sentences in exposition, numbers written the way they're spoken, no parentheticals, and rhetorical
   questions that set up reveals.
2. **Performance markup.** Claude annotates the script:
   `[beat]` for a pause, `*emphasis*`, `{pace: slow}` for reveals and `{pace: urgent}` for chases.
   The TTS adapter turns these into pause lengths, chunk boundaries and per-chunk expressiveness
   settings (for example Chatterbox `exaggeration` / `cfg_weight`).
3. **One consistent narrator.** Clone the voice from a **licensed** 10–30 s reference (the
   user's own voice or a voice actor with consent). Settings stay fixed across chunks, and a
   speaker-similarity check (a local embedding model) rejects chunks where the voice drifts.
4. **Automatic retakes.** Any chunk with a whisper word-error rate above a threshold, or with
   artefacts, is regenerated with a new seed (up to N times) and the best take is kept.
5. **Mastering chain.** High-pass, gentle EQ presence boost, de-ess, compression, a subtle room
   tone, and −16 LUFS for the voice stem before the mix.
6. **Bake-off harness.** `voice/bakeoff.py` renders the same 60 s script on every installed engine
   for blind A/B listening, so we can swap in better open models as they ship.

---

## 5. Retention engine (quality bar: "surprisingly good")

Style bible rules that are enforced by the critic pass, not just suggested:

- **0–15 s:** confirm the title's promise, then an impossible-sounding specific ("In 1985, a
  company spent $4 million to make its own product worse, and it worked.")
- **15–60 s:** stack 2–3 hard specifics, open the central question, and preview the payoff.
- **Every chapter:** open a question in the first sentence, escalate, pay off, and hand off to
  the next question. Never close all loops at once.
- **A re-hook every 2–4 minutes** ("But that wasn't the strangest part…"), plus a pattern
  interrupt in the visuals (a map, a document or a chapter card).
- **Midpoint reversal**, then a climax, then a resolution that recasts the cold open.
- **No invented facts.** Anything uncertain is phrased as uncertain, and every number has a source.

Editing rules come from the Director: the hook is visually dense (a cut every 2–4 s), the body
has a cut every 4–8 s, every name, date and figure gets an on-screen label, and the music changes
at chapter boundaries.

---

## 6. Visual toolkit (all local, all template-driven)

| Template | Look |
|---|---|
| Ken Burns / parallax photo | Depth map drives a 2.5D push-in; cutout subject "pops" |
| Photo spotlight | Subject stays lit and the frame darkens, with a name and role label |
| Map | Natural Earth borders (public domain), country or city highlight, animated routes, pins, zoom to place |
| Timeline | Year ticks with an event cards slide |
| Big figure | Number counts up with context line and source |
| Document / newspaper card | Real article or scanned document, sentence highlighted, paper texture |
| Quote card | Serif pull-quote with attribution |
| Person card | Photo, name, role, years |
| Chapter card | Chapter number, title and a background plate |
| Data chart | Line or bar chart drawn on "paper" |
| Collage | Layered cutouts, pins and string, typewriter labels (built from real photo cutouts, no AI images) |

**One house style.** A single `style.json` locks fonts, palette, accent colour, grain, music
folder, pacing and density, script craft and the narrator voice. Every video looks and sounds
like the same channel, and the templates are tuned for that one look instead of being generic.

**Rendering choice:** our own HTML/CSS/GSAP templates, captured by headless Chromium through
Playwright and piped to ffmpeg. We own it fully, with no licensing questions. Remotion is free for
individuals and teams of up to 3, but needs a paid company licence beyond that, so we avoid it for
a distributed product.

---

## 7. Cost model (60-min video)

| Item | Cost |
|---|---|
| Claude (script, direction, vision) | $0 marginal (subscription) |
| TTS (local GPU / Apple Silicon) | $0 (or ~$0.10–0.40 of rented GPU time if no GPU) |
| Footage and photos (free archives + Pexels/Pixabay) | $0 |
| Cutouts, depth, CLIP, captioner, whisper (local) | $0 |
| Jev shot scoring (~2–5M input tokens) | ~$0.10–0.20 |
| Music | $0 (user library or YouTube Audio Library / CC0 tracks) |
| Electricity | ~$0.02–0.05 |
| **Total** | **~$0.15–0.25 on own hardware, < $0.70 worst case** |

This is roughly flat regardless of length. The cost doesn't scale with minutes the way per-minute voice and image APIs do.

## 8. Time budget (60-min video, target on an RTX 4070+ or M3 Pro-class machine)

The stages overlap, so the wall clock is roughly the longest chain, not the sum.

| Stage | Target | Notes |
|---|---|---|
| Packaging + research + outline | 2–3 min | Parallel subagents |
| Script (10 chapters in parallel) + critic | 3–4 min | Largest Claude step |
| Voice (60 min of audio) | 2–8 min | Kokoro is fast; Chatterbox is slower but more expressive. Pipelined per chapter. |
| Assets (search, download, rank, pick) | 4–6 min | Network-bound, parallel, pipelined |
| Graphics | 2–3 min | ~60–100 short scenes, parallel workers |
| Assembly + encode | 3–5 min | Segment-parallel hardware encode |
| QC + patch | 1–2 min | |
| **Wall clock** | **~15–25 min** | 10-min video: ~6–10 min |
| **Human time** | **~10 min** | Pick title, approve outline, watch the hook and a skim |

CPU-only laptops will be about 3–5× slower. The fix is the rented-GPU mode (still < $1). We don't
promise a 10-minute wall clock for a full hour until Phase 0 benchmarks prove it.

---

## 9. Repository layout (planned)

```
documentary/
  CLAUDE.md                 showrunner instructions for Claude Code
  .claude/skills/documentary/SKILL.md   /documentary entry point
  docfactory/
    cli.py                  `doc make "<topic>" --minutes 60 --style noir`
    orchestrator.py         stage graph, per-chapter pipelining, resume
    llm.py                  claude -p wrapper, JSON schemas, retries
    research/               wikipedia, wikidata, web sources → facts.jsonl
    script/                 outline, chapter writer, critic, fact-check, markup
    voice/                  engines/{chatterbox,kokoro}.py, retake loop, mastering, bakeoff.py
    footage/                providers/{commons,archive_org,nasa,nara,loc,dvids,pexels,pixabay,youtube_optin}.py
                            keyframes.py, caption.py, rank_clip.py, judge_jev.py, contact_sheet.py, license_ledger.py
    director/               sentence → visual beat, density & variety rules
    gfx/templates/          HTML/CSS/GSAP scenes; render.py (Playwright → ffmpeg)
    photofx/                rembg cutouts, depth parallax, spotlight
    audio/                  sfx synth, music bed, ducking, loudness
    assemble/               EDL → segment render → concat
    qc/                     automated checks + Claude contact-sheet review
    publish/                thumbnails, description, chapters, credits
  style.json                the one house style (fonts, palette, pacing, voice)
  assets/                   fonts (OFL), map data (Natural Earth), textures, music/
  projects/<slug>/          every artifact of every video
```

## 10. Roadmap

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **0 — Spikes (week 1)** | TTS bake-off (Chatterbox vs Kokoro vs newest open models), Claude usage meter per 10 min of script, render-throughput test, footage coverage test on 10 sample topics, **Jev vs Claude shot-pick agreement test** | Voice that passes a blind "would you keep watching" test; measured token and time numbers |
| **1 — MVP (weeks 2–3)** | Topic → 10-min video: research, script, voice with retakes, archive footage + Ken Burns, labels, captions, music ducking | End-to-end in ≤ 15 min, $0 cash |
| **2 — Look (weeks 4–5)** | Director + template library (map, timeline, figure, document, person, chapter, spotlight, parallax), house style locked | Side-by-side against a Frontier sample is comparable or better |
| **3 — Long-form (weeks 6–7)** | Chapter engine, pipelining, segment-parallel encode, QC loop, publish kit | 60-min video in ≤ 25 min wall clock, < $1 |
| **4 — Product** | `/documentary` skill UX, local web UI, batch queue, rented-GPU mode | A non-developer makes a video in 10 min of their own time |

## 11. Risks

| Risk | Mitigation |
|---|---|
| Claude plan limits on long videos | Measure in Phase 0, send only compact JSON through Claude, prefilter with CLIP, cache research |
| Thin public-domain footage for modern topics | Photo motion, maps and document graphics; opt-in short fair-use clips with a warning |
| TTS sounds "AI" | Direction markup, retakes, mastering, licensed voice clone, bake-off to keep the best engine |
| YouTube "inauthentic/mass-produced content" policy | Real research, original scripts with a point of view, transformative editing, human review step, sources in description |
| Voice cloning misuse | Only clone voices the user owns or has written consent for, and record the consent in the project |
| Factual errors | Every claim is sourced, a fact-check pass runs, and uncertainty is phrased as uncertainty |
