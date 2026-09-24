"""Produce a documentary from a script: narration → one collage scene per beat → assembled video.

    python -m pipeline.produce projects/new-coke --media projects/new-coke/footage/media \
        --out out/new-coke --feed editor/apps/web/public/studio-feed

Inputs in the project folder:
  script.json   chapters → beats, each with narration text, a `scene` (collage renderer spec without
                media/duration) and a `request` (what footage it needs). Written by the scriptwriter.
  media         the footage the finder picked, one file per beat, named `<beat id>.<ext>` (from
                `footage.fetch`), plus `sources.json` with licences.

Every beat becomes a scene clip exactly as long as its narration (plus its pause), so picture and
voice stay in sync. Beats whose footage is missing get their scripted fallback scene, or a clearly
marked "needs footage" card so a human sees the gap in review instead of random filler.
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


def _render_one(args: tuple[dict, str]) -> str:
    from collage.render import render_scene  # imported in the worker process
    spec, out = args
    render_scene(spec, Path(out))
    return out


def produce(project: Path, out: Path, media_dir: Path | None, feed: Path | None, workers: int | None) -> Path:
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

    # 1. narration, one take per beat, exact timings
    voice = script.get("voice", {})
    status.stage("voice", "running", f"Narrating {len(beats)} beats")
    narrator = Narrator(voice.get("voice", "bm_george"), float(voice.get("speed", 0.95)))
    times = narrator.narrate(chapters, project / "narration.wav")
    (project / "timings.json").write_text(json.dumps(times))
    total = times[-1][-1][1]
    status.stage("voice", "done", f"{total:.0f}s of narration ({narrator.device}) in {time.time() - t0:.0f}s")

    # 2. one scene per beat, frame-exact durations
    flat = [t for ch in times for t in ch]
    jobs, report, frame = [], [], 0
    for b, (s, e) in zip(beats, flat):
        end_f = round(e * FPS)
        n = max(1, end_f - frame)
        frame += n
        media = find_media(media_dir, b["id"])
        spec, kind = scene_for(b, media, n / FPS)
        clip = scenes_dir / f"{b['id']}.mp4"
        (scenes_dir / f"{b['id']}.json").write_text(json.dumps(spec, indent=1))
        jobs.append((spec, str(clip)))
        report.append({"beat": b["id"], "scene": spec["type"], "resolved": kind,
                       "media": str(media) if media else None, "seconds": round(n / FPS, 2)})
    missing = sum(r["resolved"] == "missing" for r in report)
    status.stage("edit", "running", f"Rendering {len(jobs)} collage scenes ({missing} need footage)")
    t1 = time.time()
    workers = workers or max(1, (os.cpu_count() or 2) // 2)
    done = 0
    with ProcessPoolExecutor(workers) as ex:
        futures = [ex.submit(_render_one, j) for j in jobs]
        for f in as_completed(futures):
            f.result()
            done += 1
            if done % 5 == 0 or done == len(jobs):
                status.log("edit", f"{done}/{len(jobs)} scenes rendered")
    status.stage("edit", "done", f"{len(jobs)} scenes in {time.time() - t1:.0f}s ({missing} need footage)")

    # 3. assemble: scenes back to back, narration + music, loudness-mastered
    status.stage("render", "running", "Assembling the final cut")
    video = out / "video.mp4"
    render.concat([Path(c) for _, c in jobs], video)
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
        "stages": {"voice": round(t1 - t0, 1), "scenes": round(time.time() - t1, 1)},
        "speed_vs_realtime": round(render.duration(final) / seconds, 2), "beats": report}, indent=1))
    status.stage("render", "done", f"{render.duration(final):.0f}s documentary in {seconds:.0f}s total")
    return final


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("--media", help="folder of picked footage named <beat id>.<ext>")
    ap.add_argument("--out", help="default: out/<project name>")
    ap.add_argument("--feed", help="publish progress + result to this Studio feed folder")
    ap.add_argument("--workers", type=int)
    a = ap.parse_args(argv)
    project = Path(a.project).resolve()
    final = produce(project, Path(a.out or f"out/{project.name}").resolve(),
                    Path(a.media).resolve() if a.media else None,
                    Path(a.feed).resolve() if a.feed else None, a.workers)
    print(final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
