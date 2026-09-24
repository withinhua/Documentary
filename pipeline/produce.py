"""Produce a documentary from a script: narration → one collage scene per beat → assembled video.

    python -m pipeline.produce projects/new-coke --media projects/new-coke/footage/media \
        --out out/new-coke --feed editor/apps/web/public/studio-feed
    python -m pipeline.produce projects/new-coke --burst 6        # scenes on 6 rented CPU boxes

Inputs in the project folder:
  script.json   chapters → beats, each with narration text, a `scene` (collage renderer spec without
                media/duration) and a `request` (what footage it needs). Written by the scriptwriter.
  media         the footage the finder picked, one file per beat, named `<beat id>.<ext>` (from
                `footage.fetch`), plus `sources.json` with licences.

Every beat becomes a scene clip exactly as long as its narration (plus its pause), so picture and
voice stay in sync. Beats whose footage is missing get their scripted fallback scene, or a clearly
marked "needs footage" card so a human sees the gap in review instead of random filler.

Speed: narration runs in parallel processes and every scene starts rendering the moment its beat's
timing is known (voice and edit overlap), in a process pool sized to the machine. Clips live in a
content-addressed cache (`<out>/.render-cache/<key>.mp4`, key = spec + media size/mtime + encoder +
renderer code) and voice takes in `<out>/.tts-cache`, so a re-run after an edit only re-voices the
lines and re-renders the scenes that changed. `--burst N` renders the scenes on N rented vast.ai
CPU machines instead (see burst/scenes.py) and concatenates the clips here.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .status import Status

FPS = 30
MEDIA_EXT = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".mp4", ".mov", ".webm", ".mkv")
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv")


def find_media(media_dir: Path | None, beat_id: str) -> Path | None:
    if not media_dir or not media_dir.is_dir():
        return None
    for p in sorted(media_dir.iterdir()):
        if p.stem == beat_id and p.suffix.lower() in MEDIA_EXT:
            return p
    return None


def scene_for(beat: dict, media: Path | None, duration: float) -> tuple[dict, str]:
    """Collage spec for a beat, plus how it was resolved: 'footage', 'no-media', 'fallback' or 'missing'."""
    scene = dict(beat.get("scene") or {"type": "title", "kicker": "", "title": beat.get("text", "")[:60]})
    request = beat.get("request") or {}
    needs = request.get("kind") in ("photo", "video")
    kind = "no-media"
    if needs and media:
        key = "video" if media.suffix.lower() in VIDEO_EXT else "photo"
        if scene.get("type") == "full":
            scene.pop("photo", None)
            scene.pop("video", None)
            scene[key] = str(media)
        elif key == "photo":
            scene["photo"] = str(media)
        else:  # a video picked for a photo-style scene: show it full-frame instead
            scene = {"type": "full", "video": str(media), "motion": "push", "style": "archival",
                     **({"caption": scene["caption"]} if scene.get("caption") else {})}
        kind = "footage"
    elif needs:
        if request.get("fallback") or scene.get("fallback"):
            scene = dict(request.get("fallback") or scene.get("fallback"))
            kind = "fallback"
        elif scene.get("type") in ("number_card", "quote", "headline", "title"):
            scene.pop("photo", None)  # these scenes work without a picture
            kind = "fallback"
        else:
            scene = {"type": "title", "kicker": "NEEDS FOOTAGE", "title": request.get("subject", "")[:70]}
            kind = "missing"
    if scene.get("type") == "headline" and scene.get("paraphrase"):
        # Unverified wording must not look like a real newspaper: show it as a typewriter card.
        scene = {"type": "title", "kicker": str(scene.get("date") or ""), "title": scene.get("text", "")}
    scene.pop("fallback", None)
    scene.pop("paraphrase", None)
    scene["duration"] = round(duration, 3)
    return scene, kind


def _render_one(args: tuple[dict, str, int, int]) -> str:
    from collage.render import render_scene  # imported in the worker process
    spec, out, threads, enc_threads = args
    render_scene(spec, Path(out), threads=threads, encoder_threads=enc_threads)
    return out


class ClipCache:
    """Content-addressed store of rendered scene clips: `<root>/<key>.mp4`. A beat's clip in the
    scenes folder is a hard link to (or copy of) its cache entry, so beats that move, or scenes shared
    by several beats, never render twice."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        return self.root / f"{key}.mp4"

    def has(self, key: str) -> bool:
        p = self.path(key)
        return p.is_file() and p.stat().st_size > 0

    def place(self, key: str, dest: Path) -> None:
        src = self.path(key)
        try:
            if dest.exists() and os.path.samefile(src, dest):
                return
        except OSError:
            pass
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.unlink(missing_ok=True)
        try:
            os.link(src, tmp)
        except OSError:
            shutil.copyfile(src, tmp)
        os.replace(tmp, dest)


def plan_workers(cores: int | None = None, workers: int | None = None) -> tuple[int, int, int]:
    """(scene processes, compositing threads per scene, x264 threads per scene).
    A scene's x264 encode costs ~2x its compositing, so ~2 cores per scene process keeps every core
    busy without one long scene serialising the end of the run."""
    cores = cores or os.cpu_count() or 2
    workers = workers or max(1, min(cores, max(2, cores // 2)))
    return workers, 2, 2


def produce(project: Path, out: Path, media_dir: Path | None, feed: Path | None, workers: int | None,
            burst: int = 0, burst_opts: dict | None = None, cache: bool = True) -> Path:
    import multiprocessing as mp

    from collage.render import cache_key
    from worker import render
    from worker.tts import Narrator

    t0 = time.time()
    script = json.loads((project / "script.json").read_text())
    slug = script.get("slug") or project.name
    publish = None
    if feed:
        from .feed import publish as _publish
        publish = lambda: _publish(project, feed, out)  # noqa: E731
    status = Status(project, on_change=publish)
    out.mkdir(parents=True, exist_ok=True)
    scenes_dir = out / "scenes"
    scenes_dir.mkdir(exist_ok=True)
    clips = ClipCache(out / ".render-cache")

    chapters = script["chapters"]
    beats = [b for ch in chapters for b in ch["beats"]]
    for i, b in enumerate(beats):
        b.setdefault("id", f"b{i + 1:03d}")
    # A first job.json (script only) so the Studio shows the production from the first stage on.
    (project / "job.json").write_text(json.dumps({
        **{k: script[k] for k in ("slug", "title", "topic", "voice") if k in script},
        "chapters": [{"id": ch.get("id"), "title": ch.get("title"),
                      "beats": [{"text": b["text"], "visual": {}} for b in ch["beats"]]} for ch in chapters],
    }, indent=1))

    # 1+2. narration and scenes, overlapped: a beat's scene is queued as soon as its slot is known
    voice = script.get("voice", {})
    status.stage("voice", "running", f"Narrating {len(beats)} beats")
    narrator = Narrator(voice.get("voice", "bm_george"), float(voice.get("speed", 0.95)),
                        cache_dir=(out / ".tts-cache") if cache else None)
    n_proc, threads, enc_threads = plan_workers(workers=workers)
    pool = None if burst else ProcessPoolExecutor(n_proc, mp_context=mp.get_context("spawn"))
    jobs, report, futures, got = [], [], {}, {}
    timing = {"first_submit": None}
    beat_index = {(ci, bi): b for ci, ch in enumerate(chapters) for bi, b in enumerate(ch["beats"])}

    def on_beat(ci: int, bi: int, s: float, e: float) -> None:
        b = beat_index[(ci, bi)]
        n = max(1, round(e * FPS) - round(s * FPS))   # slots are whole frames (frame_rate below)
        media = find_media(media_dir, b["id"])
        spec, kind = scene_for(b, media, n / FPS)
        clip = scenes_dir / f"{b['id']}.mp4"
        (scenes_dir / f"{b['id']}.json").write_text(json.dumps(spec, indent=1))
        key = cache_key(spec)
        job = {"id": b["id"], "spec": spec, "key": key, "clip": clip, "frames": n}
        jobs.append(job)
        hit = cache and clips.has(key)
        report.append({"beat": b["id"], "scene": spec["type"], "resolved": kind,
                       "media": str(media) if media else None, "seconds": round(n / FPS, 2),
                       "cached": bool(hit)})
        if hit:
            clips.place(key, clip)
        elif pool is not None:
            timing["first_submit"] = timing["first_submit"] or time.time()
            futures[pool.submit(_render_one, (spec, str(clips.path(key)), threads, enc_threads))] = job

    try:
        times = narrator.narrate(chapters, project / "narration.wav", on_beat=on_beat, frame_rate=FPS)
        (project / "timings.json").write_text(json.dumps(times))
        total = times[-1][-1][1]
        t_voice = time.time()
        status.stage("voice", "done", f"{total:.0f}s of narration ({narrator.device}, {narrator.cached} cached takes)"
                                      f" in {t_voice - t0:.0f}s")

        missing = sum(r["resolved"] == "missing" for r in report)
        cached = sum(r["cached"] for r in report)
        todo = [j for j in jobs if not (cache and clips.has(j["key"]))]
        status.stage("edit", "running", f"Rendering {len(todo)} collage scenes ({cached} cached, "
                                        f"{missing} need footage)")
        if burst and todo:
            from burst.scenes import run_scene_burst
            got = run_scene_burst(todo, burst, out / ".burst", log=lambda m: status.log("edit", m),
                                  **(burst_opts or {}))
            for j in todo:
                if j["id"] in got:
                    os.replace(got[j["id"]], clips.path(j["key"]))
            left = [j for j in todo if not clips.has(j["key"])]
            if left:  # anything the fleet didn't deliver is rendered here
                status.log("edit", f"burst returned {len(todo) - len(left)}/{len(todo)} clips; rendering {len(left)} locally")
                pool = ProcessPoolExecutor(n_proc, mp_context=mp.get_context("spawn"))
                for j in left:
                    futures[pool.submit(_render_one, (j["spec"], str(clips.path(j["key"])), threads, enc_threads))] = j
        done = 0
        for f in as_completed(futures):
            f.result()
            done += 1
            if done % 5 == 0 or done == len(futures):
                status.log("edit", f"{done}/{len(futures)} scenes rendered")
    finally:
        if pool is not None:
            pool.shutdown(cancel_futures=True)
    for j in jobs:
        clips.place(j["key"], j["clip"])
    t_scenes = time.time()
    n_cached = sum(r["cached"] for r in report)
    status.stage("edit", "done", f"{len(jobs)} scenes ready {t_scenes - t0:.0f}s after start: {len(futures)} rendered here"
                                 + (f", {len(got)} on the burst fleet" if burst else "") + f", {n_cached} cached")

    # 3. assemble: scenes back to back, narration + music, loudness-mastered
    status.stage("render", "running", "Assembling the final cut")
    video = out / "video.mp4"
    render.concat([j["clip"] for j in jobs], video)
    music = next(iter(sorted((project / "music").glob("*.*"))), None) if (project / "music").is_dir() else None
    audio = out / "audio.m4a"
    render.mix_audio(project / "narration.wav", audio, music)
    final = out / "final.mp4"
    render.mux(video, audio, final)
    video.unlink()

    # 4. hand the production to the Studio: scene clips are the timeline's footage
    job = {k: script[k] for k in ("slug", "title", "topic", "voice") if k in script}
    job["style"] = {"title": script.get("title")}
    sources_path = (media_dir / "sources.json") if media_dir else None
    if not (sources_path and sources_path.is_file()) and media_dir:
        sources_path = media_dir.parent / "sources.json"
    job["sources"] = json.loads(sources_path.read_text()) if sources_path and sources_path.is_file() else {}
    local_scenes = project / "scenes"
    if local_scenes.resolve() != scenes_dir.resolve():
        shutil.copytree(scenes_dir, local_scenes, dirs_exist_ok=True)

    def studio_beat(b: dict) -> dict:
        beat = {"text": b["text"], "pace": b.get("pace", 1.0),
                "visual": {"type": "clip", "src": f"scenes/{b['id']}.mp4", "in": 0}}
        if b.get("pause_after") is not None:
            beat["pause_after"] = b["pause_after"]
        return beat

    job["chapters"] = [{"id": ch.get("id"), "title": ch.get("title"),
                        "beats": [studio_beat(b) for b in ch["beats"]]} for ch in chapters]
    (project / "job.json").write_text(json.dumps(job, indent=1))
    seconds = round(time.time() - t0, 1)
    (out / "report.json").write_text(json.dumps({
        "seconds": seconds, "video_seconds": round(render.duration(final), 2), "encoder": "collage + libx264",
        "stages": {"voice": round(t_voice - t0, 1),
                   "scenes": round(t_scenes - (timing["first_submit"] or t_voice), 1),
                   "scenes_after_voice": round(max(0.0, t_scenes - t_voice), 1),
                   "assemble": round(time.time() - t_scenes, 1)},
        "workers": {"scene_processes": n_proc, "threads_per_scene": threads, "x264_threads": enc_threads,
                    "burst_machines": burst or 0},
        "rendered": len(futures), "rendered_burst": len(got), "cached": n_cached,
        "speed_vs_realtime": round(render.duration(final) / seconds, 2), "beats": report}, indent=1))
    status.stage("render", "done", f"{render.duration(final):.0f}s documentary in {seconds:.0f}s total")
    return final


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("--media", help="folder of picked footage named <beat id>.<ext>")
    ap.add_argument("--out", help="default: out/<project name>")
    ap.add_argument("--feed", help="publish progress + result to this Studio feed folder")
    ap.add_argument("--workers", type=int, help="scene render processes (default: about cores/2)")
    ap.add_argument("--no-cache", action="store_true", help="re-voice and re-render everything")
    ap.add_argument("--burst", type=int, default=0, metavar="N",
                    help="render the scenes on N rented vast.ai CPU machines (needs VAST_API_KEY, "
                         "S3_* and BURST_IMAGE; see burst/scenes.py)")
    ap.add_argument("--burst-min-cpu", type=int, default=32, help="with --burst: min effective CPU cores per box")
    ap.add_argument("--burst-deadline", type=int, default=1800, help="with --burst: hard stop in seconds")
    ap.add_argument("--burst-image", default=os.environ.get("BURST_IMAGE", ""))
    a = ap.parse_args(argv)
    project = Path(a.project).resolve()
    burst_opts = {"min_cpu": a.burst_min_cpu, "deadline": a.burst_deadline, "image": a.burst_image}
    final = produce(project, Path(a.out or f"out/{project.name}").resolve(),
                    Path(a.media).resolve() if a.media else None,
                    Path(a.feed).resolve() if a.feed else None, a.workers,
                    burst=a.burst, burst_opts=burst_opts, cache=not a.no_cache)
    print(final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
