"""Execute one prepared shard: fetch → voice → build edit → render → upload. No decisions, no LLM.

    python -m worker.worker --local projects/demo --out out/demo     # test on any machine
    python -m worker.worker --remote                                  # on the rented GPU (env from launcher)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--local")
    ap.add_argument("--out", default="out/local")
    ap.add_argument("--remote", action="store_true")
    ap.add_argument("--feed", help="also publish progress + results to this Studio feed folder")
    a = ap.parse_args()
    if a.remote:
        remote()
    elif a.local:
        local(Path(a.local).resolve(), Path(a.out).resolve(), Path(a.feed).resolve() if a.feed else None)
    else:
        ap.error("--local JOB_DIR or --remote")
