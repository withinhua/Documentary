"""Publish productions to the Studio feed, the folder the Studio UI reads.

    python -m pipeline.feed publish projects/demo --renders out/demo --feed editor/apps/web/public/studio-feed

For each production it writes `<feed>/<slug>/production.json` (script with beat timings, footage
with licences, narration, renders, stage status and activity), plus lightweight browser proxies
(720p H.264, JPEG thumbnails, MP3 narration). Full-resolution media stays with the project for
the final render. `<feed>/index.json` lists every production.

The feed is plain static files, so it can be served by the Vite dev server, any static host, or
an R2 bucket with public read.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff")
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def _probe(path: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return round(float(p.stdout.strip()), 3)
    except ValueError:
        return 0.0


def _fresh(src: Path, dst: Path) -> bool:
    return dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime


def proxy_video(src: Path, dst: Path, thumb: Path) -> None:
    if not _fresh(src, dst):
        _ff("-i", str(src), "-vf", "scale=-2:720", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(dst))
    if not _fresh(src, thumb):
        _ff("-ss", "1", "-i", str(src), "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(thumb))


def proxy_image(src: Path, dst: Path, thumb: Path) -> None:
    if not _fresh(src, dst):
        _ff("-i", str(src), "-vf", "scale='min(1920,iw)':-2", "-q:v", "3", str(dst))
    if not _fresh(src, thumb):
        _ff("-i", str(src), "-vf", "scale=480:-2", "-q:v", "4", str(thumb))


def media_type(src: str) -> str:
    s = src.lower()
    return "image" if s.endswith(PHOTO_EXT) else "audio" if s.endswith(AUDIO_EXT) else "video"


def publish(project: Path, feed: Path, renders: Path | None = None) -> Path:
    if (project / "job.json").is_file():
        job = json.loads((project / "job.json").read_text())
    else:  # before the first render: show the script (no pictures yet) so the production appears early
        script = json.loads((project / "script.json").read_text())
        job = {**{k: script[k] for k in ("slug", "title", "topic", "voice") if k in script},
               "chapters": [{"id": c.get("id"), "title": c.get("title"),
                             "beats": [{"text": b["text"], "visual": {}} for b in c["beats"]]}
                            for c in script["chapters"]]}
    slug = job.get("slug") or project.name
    out = feed / slug
    (out / "media").mkdir(parents=True, exist_ok=True)
    (out / "renders").mkdir(parents=True, exist_ok=True)
    status_path = project / "status.json"
    status = json.loads(status_path.read_text()) if status_path.is_file() else {"stages": [], "activity": []}
    timings_path = project / "timings.json"
    timings = json.loads(timings_path.read_text()) if timings_path.is_file() else None
    sources = job.get("sources", {})

    # footage + photos → proxies, with where each one is used
    media: dict[str, dict] = {}
    chapters = []
    for ci, ch in enumerate(job.get("chapters", [])):
        beats = []
        for bi, b in enumerate(ch.get("beats", [])):
            v = dict(b.get("visual") or {})
            src = v.get("src")
            if src and src not in media and (project / src).is_file():
                kind = media_type(src)
                stem = Path(src).stem
                item = {"id": stem, "name": Path(src).name, "type": kind, "usedIn": [],
                        **sources.get(src, {})}
                if kind == "video":
                    proxy_video(project / src, out / "media" / f"{stem}.mp4", out / "media" / f"{stem}.thumb.jpg")
                    item.update(src=f"media/{stem}.mp4", thumb=f"media/{stem}.thumb.jpg",
                                durationSec=_probe(project / src))
                elif kind == "image":
                    proxy_image(project / src, out / "media" / f"{stem}.jpg", out / "media" / f"{stem}.thumb.jpg")
                    item.update(src=f"media/{stem}.jpg", thumb=f"media/{stem}.thumb.jpg")
                media[src] = item
            if src in media:
                media[src]["usedIn"].append(f"{ci}.{bi}")
                v["mediaId"] = media[src]["id"]
            beat = {"text": b.get("text", ""), "visual": v, "label": b.get("label"),
                    "pace": b.get("pace", 1.0)}
            if timings and ci < len(timings) and bi < len(timings[ci]):
                beat["start"], beat["end"] = timings[ci][bi]
            beats.append(beat)
        chapters.append({"id": ch.get("id", f"ch{ci}"), "title": ch.get("title") or ch.get("id", f"Chapter {ci + 1}"),
                         "beats": beats})

    narration = None
    if (project / "narration.wav").is_file():
        dst = out / "narration.mp3"  # MP3: every browser + the editor's importer accept it
        if not _fresh(project / "narration.wav", dst):
            _ff("-i", str(project / "narration.wav"), "-c:a", "libmp3lame", "-b:a", "160k", str(dst))
        narration = {"src": "narration.mp3", "durationSec": _probe(dst)}

    outputs = []
    if renders and renders.is_dir():
        report = json.loads((renders / "report.json").read_text()) if (renders / "report.json").is_file() else {}
        for name, kind in (("final.mp4", "final"), ("shard.mp4", "render")):
            f = renders / name
            if f.is_file():
                dst = out / "renders" / name
                thumb = out / "renders" / f"{f.stem}.thumb.jpg"
                proxy_video(f, dst, thumb)
                outputs.append({"kind": kind, "src": f"renders/{name}", "thumb": f"renders/{f.stem}.thumb.jpg",
                                "durationSec": _probe(f), "sizeBytes": f.stat().st_size, **report})
        if (renders / "project.mlt").is_file():
            shutil.copy(renders / "project.mlt", out / "renders" / "project.mlt")

    production = {
        "coverage": coverage(project), "slug": slug, "title": job.get("title", slug), "topic": job.get("topic"),
        "voice": job.get("voice"), "style": job.get("style"),
        "createdAt": status.get("createdAt"), "updatedAt": time.time(),
        "stages": status.get("stages", []), "activity": status.get("activity", []),
        "script": {"chapters": chapters}, "media": list(media.values()),
        "narration": narration, "outputs": outputs,
    }
    (out / "production.json").write_text(json.dumps(production, indent=1))
    _write_index(feed)
    return out


def coverage(project: Path) -> dict | None:
    """How much of the script has real footage: beats needing a picture, approved, downloaded."""
    req_path, picks_path = project / "requests.json", project / "footage" / "picks.json"
    if not req_path.is_file():
        return None
    needed = {r["id"] for r in json.loads(req_path.read_text()) if r.get("kind") in ("photo", "video")}
    picks = json.loads(picks_path.read_text()) if picks_path.is_file() else []
    approved = {p["id"] for p in picks if p.get("pick")} & needed
    media = project / "footage" / "media"
    downloaded = {f.stem for f in media.iterdir() if f.is_file()} & needed if media.is_dir() else set()
    fair = sum(1 for p in picks if p.get("pick") and "fair use" in (p.get("notes") or "").lower())
    return {"needed": len(needed), "approved": len(approved), "downloaded": len(downloaded),
            "fair_use": fair, "reviewed": len({p["id"] for p in picks} & needed)}


def _write_index(feed: Path) -> None:
    items = []
    for p in sorted(feed.glob("*/production.json")):
        d = json.loads(p.read_text())
        running = next((s for s in d["stages"] if s.get("status") == "running"), None)
        failed = next((s for s in d["stages"] if s.get("status") == "failed"), None)
        done = sum(1 for s in d["stages"] if s.get("status") in ("done", "skipped"))
        rendered = any(s["id"] == "render" and s.get("status") == "done" for s in d["stages"])
        final = next((o for o in d["outputs"] if o["kind"] == "final"), None) or next(iter(d["outputs"]), None)
        thumb = final["thumb"] if final else next((m["thumb"] for m in d["media"] if m.get("thumb")), None)
        items.append({
            "slug": d["slug"], "title": d["title"], "updatedAt": d["updatedAt"],
            "state": "failed" if failed else "running" if running else
                     "done" if d["stages"] and done == len(d["stages"]) else "ready" if rendered else "draft",
            "stage": (running or failed or {}).get("label"),
            "progress": [done, len(d["stages"])],
            "thumb": f"{d['slug']}/{thumb}" if thumb else None,
            "durationSec": (final or {}).get("durationSec") or (d.get("narration") or {}).get("durationSec"),
            "detail": (running or failed or {}).get("detail"),
            "coverage": d.get("coverage"),
            "beats": sum(len(c["beats"]) for c in d["script"]["chapters"]),
            "chapters": len(d["script"]["chapters"]),
        })
    items.sort(key=lambda x: -x["updatedAt"])
    (feed / "index.json").write_text(json.dumps({"productions": items, "updatedAt": time.time()}, indent=1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish")
    p.add_argument("project")
    p.add_argument("--renders")
    p.add_argument("--feed", default="editor/apps/web/public/studio-feed")
    a = ap.parse_args(argv)
    out = publish(Path(a.project), Path(a.feed), Path(a.renders) if a.renders else None)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
