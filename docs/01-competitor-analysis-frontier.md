# Competitor analysis: Frontier ("Frontier Access" on Whop)

Product page: https://whop.com/frontierhq/frontier-access/ (made by Primal Robin, sold on Whop)

> The Whop page itself is blocked from our research environment, so this analysis comes from the
> product's own README and `CLAUDE.md` in a public GitHub copy of "Frontier 4"
> (`github.com/moulidanimates-pixel/frontiter`) and from search-result summaries of the listing.
> We read it only to learn what the product does. **We don't copy any of its code.** It is a paid
> product, and its licence forbids redistribution.

## What it is

It's a faceless-video studio that runs on the buyer's own computer. It is a Python app
(about 23k lines) with a local web UI (`python app.py`). Claude Code handles setup and
customisation, and Claude also writes every script, prompt and editing decision.

You enter a title or paste a script. It writes the narration, records a voice, finds footage,
draws graphics, animates maps and headlines, and adds sound design. It then cuts everything
into a 1080p MP4 with a thumbnail and a YouTube description.

## Pipeline (as it's built)

| Stage | How Frontier does it | Who pays |
|---|---|---|
| Script | Claude (Claude Code or API), per-style prompts, "retention craft" (a hook in the first seconds, a chain of payoffs, the title's payoff foreshadowed) | Claude plan |
| Voice | ElevenLabs voices through resellers (Algrow, WaveSpeed, ai33) | **Paid per character** |
| Footage | Claude writes a YouTube search for every ~30 s of narration. **Gemini watches the videos** and logs every shot (start/end/what it shows/quality). Claude picks shots. yt-dlp downloads only those seconds, cropped clear of logos. | Gemini/WaveSpeed/kie/Algrow (cheap, often free tier) |
| Stock | Pexels b-roll | Free |
| Images | gpt-image-2 / Seedream through Algrow, WaveSpeed or kie.ai | **Paid per image** |
| Photo FX | Photo spotlight, real-object cutouts, depth-pop, Vox-style paper collage, all via WaveSpeed | **Paid** |
| Graphics | Python-rendered: maps (real borders, pins, routes), newspaper article animations, counters, timelines, charts, lower thirds | Local |
| Director | `director.py`: Claude decides, sentence by sentence, what goes on top of the footage. The first minute gets the densest treatment; after that it's about one collage and one graphic per minute. | Claude plan |
| Sound | Synthesised SFX (whooshes, hits, risers), user-supplied music bed with ducking, mastered to −14 LUFS | Local |
| Output | Burnt-in subtitles, thumbnail, description with chapters and sources | Local + Claude |
| QC | `check_video.py` checks pacing, repetition, language and audio | Local |

### Styles and prices (their own figures)

| Style | Look | Cost |
|---|---|---|
| Documentary (default) | Real YouTube footage, maps, newspapers, real photos, Vox collage, premium graphics | **~$0.50 per minute, so ~$30 for an hour** |
| "Carl Jung" cheap mode | Stock footage + AI images + motion graphics | ~$1–2 per 30 min |
| 2D Stories | Flat 2D character images | ~$0.15 per minute |

> **Correction from the team:** in practice, Frontier users are producing Documentary-style videos
> for **under $1**. The README's $0.50/min figure is a list-price estimate with every option on.
> **Cost alone is not our advantage.** We win on speed at long-form lengths, monetisation-safe
> footage, narration quality and one polished, consistent house style.

### Speed (their own figure)

> "A 10-minute Documentary takes roughly 40–60 minutes on a laptop."

At that rate, an hour-long documentary would take about **4–6 hours** of rendering.

## What they do well (we should match it)

1. **Editing tied to the narration.** Every visual is picked for the sentence it sits on, and names, dates and numbers get on-screen labels.
2. **A hook-heavy first minute**, then a steadier rhythm afterwards.
3. **Real material instead of AI art** for documentaries: footage, photos, articles and maps.
4. **Retention framework** baked into the prompts, with separate story and teaching modes.
5. **Styles as data** (one JSON per channel) and title-aware variants.
6. **Claude Code as the onboarding and customisation interface.** Non-developers can change the tool by asking in plain English.
7. **Cost preview before rendering.** Each feature shows its per-minute cost.
8. **Sound design is synthesised**, so there is nothing to license.

## Where they're weak (our openings)

| Weakness | Why it matters | Our answer |
|---|---|---|
| Per-minute API costs (voice, images, photo FX) that grow with length. They're cheap when tuned, but they still scale. | Long-form multiplies every per-minute cost | Local open-weights TTS, local cutouts and depth, no image generation. **Target: < $1 per hour, flat regardless of length.** |
| **Several third-party accounts** (Algrow, WaveSpeed, kie, Gemini, Pexels, ai33) with reserves and fallbacks | Setup friction, surprise bills, vendor risk | **One dependency: the user's Claude subscription.** Everything else is free APIs or local models. |
| **Slow**: 40–60 min per 10 min of video | An hour-long video takes an afternoon | A pipelined, parallel, GPU-aware renderer that works on chapters while the script is still being written |
| **YouTube-ripped footage** (they admit that "a rights holder can still claim a video") | Content ID claims redirect or block monetisation, and repeat strikes can end a channel | **Monetisation-safe by default**: public-domain, Creative Commons and government archives, with a licence ledger and auto-generated credits. YouTube clips are an explicit opt-in. |
| Shot logging needs Gemini to watch video | Slow at long-form scale (hundreds of shots) | Local keyframe captioning, then **Jev** (a fast, cheap classification model) scores every candidate against every sentence. **Claude vision** only confirms the shortlist. |
| Built for ~10-minute videos | Long-form (40–90 min) needs a different structure: chapters, re-hooks, a midpoint turn, consistent voice for an hour | **Built for long-form first**: an act and chapter engine with re-hooks every 2–4 minutes and voice-consistency checks |
| Voice quality depends on the ElevenLabs reseller | Cost is tied to quality | A voice-direction layer (performance markup, whisper-verified retakes, a mastering chain) that makes free models sound directed rather than just read |

## Bottom line

Frontier proves the concept: a title can become a watchable documentary with Claude directing
an ffmpeg pipeline. Its business model passes API costs to the buyer, and its footage strategy
puts monetisation at risk. Our product should keep its best editing ideas (sentence-level
direction, a dense hook, real material, a style defined in one file) and rebuild the expensive and slow parts
around **local models + fast classification (Jev) + Claude vision + free archives**. The result costs
the same whatever the length, is several times faster at long-form, and is safe to monetise.
