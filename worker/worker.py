"""Execute one prepared shard: fetch → voice → build edit → render → upload. No decisions, no LLM.

    python -m worker.worker --local projects/demo --out out/demo     # test on any machine
    python -m worker.worker --remote                                  # on the rented GPU (env from launcher)

Scene mode (collage clips for `pipeline.produce --burst N`, see burst/scenes.py):

    python -m worker.worker --scenes --remote                         # on the rented CPU box
    python -m worker.worker --scenes --local scenes-00.tar --out clips/   # test a bundle on any machine
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from . import mlt, render
from .tts import Narrator

HOUSE = Path(os.environ.get("HOUSE_DIR", Path(__file__).resolve().parent.parent / "house"))


class Clock:
    def __init__(self):
        self.t0 = self.t = time.time()
        self.stages: dict[str, float] = {}

    def lap(self, name: str) -> None:
        now = time.time()
        self.stages[name] = round(now - self.t, 2)
        print(f"[{now - self.t0:7.1f}s] {name} ({now - self.t:.1f}s)", flush=True)
        self.t = now


# ── transfer: parallel ranged GETs over presigned URLs (datacenter links reward many streams) ──────
def fetch(url: str, dest: Path, streams: int = 16, chunk: int = 64 * 2**20) -> None:
    r = requests.get(url, headers={"Range": "bytes=0-0"}, timeout=30)
    r.raise_for_status()
    size = int(r.headers["Content-Range"].split("/")[1]) if r.status_code == 206 else None
    if not size:
        with requests.get(url, stream=True, timeout=60) as rr, open(dest, "wb") as f:
            shutil.copyfileobj(rr.raw, f, 8 * 2**20)
        return
    with open(dest, "wb") as f:
        f.truncate(size)

    def part(start: int) -> None:
        end = min(size, start + chunk) - 1
        for attempt in range(4):
            try:
                rr = requests.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=120)
                rr.raise_for_status()
                with open(dest, "r+b") as f:
                    f.seek(start)
                    f.write(rr.content)
                return
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(1 + attempt)

    with ThreadPoolExecutor(streams) as ex:
        list(ex.map(part, range(0, size, chunk)))


def put(url: str, path: Path | None = None, data: bytes | None = None) -> None:
    for attempt in range(4):
        try:
            if path is not None:
                with open(path, "rb") as f:
                    r = requests.put(url, data=f, timeout=600)
            else:
                r = requests.put(url, data=data, timeout=60)
            r.raise_for_status()
            return
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(1 + attempt)


def wait_fetch(url: str, dest: Path, deadline: float) -> None:
    while time.time() < deadline:
        r = requests.get(url, headers={"Range": "bytes=0-0"}, timeout=30)
        if r.status_code in (200, 206):
            return fetch(url, dest)
        time.sleep(2)
    raise TimeoutError("a peer shard never arrived")


# ── the shard ──────────────────────────────────────────────────────────────────────────────────
def produce(root: Path, out_dir: Path, clock: Clock, status=None) -> dict:
    job = json.loads((root / "job.json").read_text())
    stage = status.stage if status else (lambda *a, **k: None)
    (root / "house").mkdir(exist_ok=True)
    for f in ("look.cube", "vignette.png"):
        if not (root / "house" / f).exists():
            shutil.copy(HOUSE / f, root / "house" / f)

    voice = job.get("voice", {})
    n_beats = sum(len(c.get("beats", [])) for c in job["chapters"])
    stage("voice", "running", f"Narrating {n_beats} lines with {voice.get('voice', 'bm_george')}")
    narrator = Narrator(voice.get("voice", "bm_george"), float(voice.get("speed", 0.95)))
    times = narrator.narrate(job["chapters"], root / "narration.wav")
    (root / "timings.json").write_text(json.dumps(times))
    clock.lap(f"voice ({narrator.device})")
    stage("voice", "done", f"{times[-1][-1][1]:.1f}s of narration on {narrator.device} in {clock.stages[f'voice ({narrator.device})']:.0f}s")

    stage("edit", "running", "Building the timeline")
    xml, frames = mlt.build(job["chapters"], times, job.get("style"))
    project = root / "project.mlt"
    project.write_text(xml)
    clock.lap("edit")
    stage("edit", "done", f"{n_beats} beats → {frames} frames with the house look")
    stage("render", "running", "Rendering 1080p")

    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / "video.mp4"
    enc = render.render_video(project, frames, video, out_dir / "segs")
    clock.lap(f"render video ({enc})")

    music = next((root / c["music"] for c in job["chapters"] if c.get("music")), None)
    audio = out_dir / "audio.m4a"
    render.mix_audio(root / "narration.wav", audio, music)
    result = out_dir / "shard.mp4"
    render.mux(video, audio, result)
    shutil.rmtree(out_dir / "segs", ignore_errors=True)
    video.unlink()
    clock.lap("audio + mux")
    stage("render", "done", f"{render.duration(result):.1f}s video ({enc}) in {sum(v for k, v in clock.stages.items() if k.startswith(('render', 'audio'))):.0f}s")
    return {"result": result, "project": project, "frames": frames, "encoder": enc,
            "video_seconds": round(render.duration(result), 2), "shard": job.get("shard", {})}


def local(job_dir: Path, out_dir: Path, feed: Path | None = None) -> None:
    from pipeline.status import Status
    publish = None
    if feed:
        from pipeline.feed import publish as _publish
        publish = lambda: _publish(job_dir, feed, out_dir)  # noqa: E731
    clock = Clock()
    info = produce(job_dir, out_dir, clock, Status(job_dir, on_change=publish))
    shutil.copy(info["project"], out_dir / "project.mlt")
    total = round(time.time() - clock.t0, 1)
    report = {"seconds": total, "stages": clock.stages, "encoder": info["encoder"],
              "video_seconds": info["video_seconds"], "speed_vs_realtime": round(info["video_seconds"] / total, 2)}
    (out_dir / "report.json").write_text(json.dumps(report, indent=1))
    if publish:
        publish()
    print(json.dumps(report, indent=1))


def remote() -> None:
    clock = Clock()
    env = os.environ
    k = int(env["SHARD_INDEX"])
    deadline = float(env.get("DEADLINE_EPOCH", time.time() + 900))
    work = Path("/work")
    root, out = work / "job", work / "out"
    root.mkdir(parents=True, exist_ok=True)
    try:
        tar = work / "shard.tar"
        fetch(env["BUNDLE_URL"], tar)
        with tarfile.open(tar) as t:
            t.extractall(root, filter="data")
        tar.unlink()
        from burst.bundle import verify_extracted
        verify_extracted(root)
        clock.lap("download + verify")

        info = produce(root, out, clock)
        with ThreadPoolExecutor(2) as ex:
            ex.submit(put, env["RESULT_PUT_URL"], info["result"])
            ex.submit(put, env["PROJECT_PUT_URL"], info["project"])
        clock.lap("upload")
        status = {"seconds": round(time.time() - clock.t0, 1), "stages": clock.stages,
                  "encoder": info["encoder"], "video_seconds": info["video_seconds"]}
        put(env["STATUS_PUT_URL"], data=json.dumps(status).encode())

        if k == 0:  # leader: join all shards into the finished video
            peers = json.loads(env.get("PEER_URLS", "[]"))
            parts = [info["result"]]
            for i, url in enumerate(peers, start=1):
                p = out / f"peer{i:02d}.mp4"
                wait_fetch(url, p, deadline - 60)
                parts.append(p)
            final = out / "final.mp4"
            if len(parts) == 1:
                final = info["result"]
            else:
                render.concat(parts, final)
            clock.lap("join shards")
            put(env["FINAL_PUT_URL"], final)
            clock.lap("upload final")
            put(env["FINAL_STATUS_PUT_URL"], data=json.dumps({
                "seconds": round(time.time() - clock.t0, 1), "stages": clock.stages,
                "video_seconds": round(render.duration(final), 2), "shards": len(parts)}).encode())
    except Exception as e:  # report, then let run.sh self-destruct
        traceback.print_exc()
        try:
            put(env["STATUS_PUT_URL"], data=json.dumps({"error": f"{type(e).__name__}: {e}"}).encode())
        finally:
            raise


# ── scene mode: render collage scene clips from a scene bundle ─────────────────────────────────
def _rebase(v, root: Path):
    """Point the bundle-relative media paths of a spec at the extracted bundle, in place."""
    if isinstance(v, dict):
        for k, x in v.items():
            if k in ("photo", "video", "src") and isinstance(x, str) and not os.path.isabs(x):
                v[k] = str(root / x)
            else:
                _rebase(x, root)
    elif isinstance(v, list):
        for x in v:
            _rebase(x, root)
    return v


def _render_scene_job(args: tuple[dict, str, int, int]) -> str:
    from collage.render import render_scene
    spec, out, threads, enc_threads = args
    render_scene(spec, Path(out), threads=threads, encoder_threads=enc_threads)
    return out


def render_scenes(root: Path, out_dir: Path, upload=None, workers: int | None = None) -> dict:
    """Render every scene in an extracted scene bundle. `upload(scene, path)` runs for each clip as
    soon as it's done (in the background). Returns {"clips": {id: key}, "errors": {id: message}}."""
    import multiprocessing as mp
    data = json.loads((root / "scenes.json").read_text())
    # masks computed on the launcher ship in cache/; the render processes read COLLAGE_CACHE at import
    (root / "cache").mkdir(exist_ok=True)
    os.environ["COLLAGE_CACHE"] = str(root / "cache")
    out_dir.mkdir(parents=True, exist_ok=True)
    cores = os.cpu_count() or 2
    n_proc = workers or max(1, cores // 2)          # ~2 cores per scene: compositing + x264 threads
    scenes = sorted(data["scenes"], key=lambda sc: -float(sc["spec"].get("duration", 0)))  # longest first
    clips, errors = {}, {}
    with ProcessPoolExecutor(n_proc, mp_context=mp.get_context("spawn")) as ex, ThreadPoolExecutor(8) as up:
        futs = {ex.submit(_render_scene_job, (_rebase(json.loads(json.dumps(sc["spec"])), root),
                                              str(out_dir / f"{sc['id']}.mp4"), 2, 2)): sc for sc in scenes}
        uploads = {}
        for f in as_completed(futs):
            sc = futs[f]
            try:
                path = Path(f.result())
                if upload:
                    uploads[up.submit(upload, sc, path)] = sc
                else:
                    clips[sc["id"]] = sc["key"]
            except Exception as e:  # noqa: BLE001 - one bad scene must not sink the shard
                traceback.print_exc()
                errors[sc["id"]] = f"{type(e).__name__}: {e}"
            print(f"scene {sc['id']}: {'ok' if sc['id'] not in errors else 'FAILED'} "
                  f"({len(clips) + len(uploads) + len(errors)}/{len(scenes)})", flush=True)
        for f in as_completed(uploads):
            sc = uploads[f]
            try:
                f.result()
                clips[sc["id"]] = sc["key"]
            except Exception as e:  # noqa: BLE001
                errors[sc["id"]] = f"upload: {type(e).__name__}: {e}"
    return {"clips": clips, "errors": errors}


def _open_bundle(src: Path, root: Path) -> Path:
    if src.is_dir():
        return src
    with tarfile.open(src) as t:
        t.extractall(root, filter="data")
    return root


def local_scenes(bundle: Path, out_dir: Path) -> dict:
    from burst.bundle import verify_extracted
    clock = Clock()
    root = _open_bundle(bundle, out_dir / "bundle")
    verify_extracted(root)
    res = render_scenes(root, out_dir)
    res["seconds"] = round(time.time() - clock.t0, 1)
    print(json.dumps(res, indent=1))
    return res


def remote_scenes() -> None:
    clock = Clock()
    env = os.environ
    work = Path("/work")
    root, out = work / "job", work / "out"
    root.mkdir(parents=True, exist_ok=True)
    try:
        tar = work / "bundle.tar"
        fetch(env["BUNDLE_URL"], tar)
        _open_bundle(tar, root)
        tar.unlink()
        from burst.bundle import verify_extracted
        verify_extracted(root)
        clock.lap("download + verify")
        res = render_scenes(root, out, upload=lambda sc, path: put(sc["put_url"], path))
        clock.lap(f"render + upload {len(res['clips'])} scenes")
        put(env["STATUS_PUT_URL"], data=json.dumps({
            "seconds": round(time.time() - clock.t0, 1), "stages": clock.stages, "cores": os.cpu_count(),
            **res}).encode())
    except Exception as e:  # report, then let run.sh self-destruct
        traceback.print_exc()
        try:
            put(env["STATUS_PUT_URL"], data=json.dumps({"error": f"{type(e).__name__}: {e}"}).encode())
        finally:
            raise


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--local")
    ap.add_argument("--out", default="out/local")
    ap.add_argument("--remote", action="store_true")
    ap.add_argument("--feed", help="also publish progress + results to this Studio feed folder")
    ap.add_argument("--scenes", action="store_true",
                    help="scene mode: render the collage clips of a scene bundle (burst/scenes.py)")
    a = ap.parse_args()
    if a.scenes or os.environ.get("WORKER_MODE") == "scenes":
        if a.remote:
            remote_scenes()
        elif a.local:
            local_scenes(Path(a.local).resolve(), Path(a.out).resolve())
        else:
            ap.error("--scenes with --remote or --local BUNDLE(.tar|dir)")
    elif a.remote:
        remote()
    elif a.local:
        local(Path(a.local).resolve(), Path(a.out).resolve(), Path(a.feed).resolve() if a.feed else None)
    else:
        ap.error("--local JOB_DIR or --remote")
