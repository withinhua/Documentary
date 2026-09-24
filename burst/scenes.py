"""Burst scene rendering: render a production's collage scenes on N rented CPU machines at once.

    python -m pipeline.produce projects/<slug> --burst 6

The collage renderer is pure CPU (numpy/OpenCV compositing + x264), so this rents the machines with
the most effective CPU cores (any GPU model) and splits the *scenes* between them:

1. Plan: scenes are independent clips, so shards need not be contiguous. Each scene gets a cost
   estimate (frames x scene-type weight, + segmentation for cut-outs) and shards are balanced
   longest-first (LPT), so every machine finishes at about the same time.
2. Bundle (before renting): one uncompressed tar per shard with `scenes.json` (specs with media
   paths rewritten into the bundle, and a presigned PUT URL per clip), the media those scenes use,
   any cut-out masks already computed locally, and a sha256 manifest.
3. Rent N machines (label docburst-<run>-kk) running the same worker image as the chapter burst,
   with `run.sh --scenes`: download, verify, render every scene with a local process pool, PUT each
   clip as soon as it's done, PUT a status JSON, self-destruct (deadline watchdog in run.sh).
4. This side polls the status objects, downloads each finished shard's clips, destroys every
   instance whatever happens, sweeps by label, and prints the cost. Scenes the fleet did not deliver
   (a failed machine, a scene error) are returned as missing; the caller renders them locally.

The rented machine only ever sees presigned URLs for its own objects, never the storage or vast.ai keys.
"""
from __future__ import annotations

import io
import json
import os
import signal
import tarfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .bundle import sha256
from .vast import DEAD_STATES, Vast, VastError, pick_cpu_offers

LABEL = "docburst"
FPS = 30

# relative CPU cost per frame by scene type (measured on the collage renderer; encode dominates)
TYPE_WEIGHT = {"title": 1.0, "number_card": 1.0, "quote": 1.0, "headline": 1.1, "cutout": 1.0, "full": 0.9}
SEGMENT_COST = 150      # ISNet cut-out on a CPU box ~= 150 frames of rendering (skipped if the mask ships)
VIDEO_WEIGHT = 1.6      # `full` scenes with a video: decode + halftone per frame


def media_refs(spec: dict) -> list[str]:
    from collage.render import spec_media
    return spec_media(spec)


def scene_cost(job: dict) -> float:
    spec = job["spec"]
    frames = job.get("frames") or round(float(spec.get("duration", 1)) * FPS)
    w = VIDEO_WEIGHT if spec.get("video") else TYPE_WEIGHT.get(spec.get("type"), 1.0)
    cost = frames * w
    if spec.get("photo") and spec.get("type") != "full" and not job.get("mask_cached"):
        cost += SEGMENT_COST
    return cost


def plan_scene_shards(jobs: list[dict], n: int) -> list[list[int]]:
    """Balance scenes over at most n shards, longest first onto the least-loaded shard (LPT).
    Deterministic; every shard non-empty; indices within a shard keep script order."""
    n = max(1, min(n, len(jobs)))
    loads = [0.0] * n
    shards: list[list[int]] = [[] for _ in range(n)]
    for i in sorted(range(len(jobs)), key=lambda i: (-scene_cost(jobs[i]), i)):
        k = min(range(n), key=lambda s: (loads[s], s))
        shards[k].append(i)
        loads[k] += scene_cost(jobs[i])
    return [sorted(s) for s in shards if s]


def _mask_files(spec: dict, cache_dir: Path) -> list[Path]:
    """Locally cached ISNet masks for the spec's photos (so remote boxes skip segmentation)."""
    import hashlib
    out = []
    for key in ("photo",):
        p = spec.get(key)
        if p and Path(p).is_file():
            h = hashlib.sha1(Path(p).read_bytes()).hexdigest()[:16]
            out += sorted(cache_dir.glob(f"mask_{h}_*.png"))
    return out


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def build_scene_bundles(jobs: list[dict], plan: list[list[int]], out_dir: Path,
                        clip_url: Callable[[dict], str], mask_cache: Path | None = None) -> list[Path]:
    """One tar per shard: scenes.json + manifest.json + media/<sha>.<ext> (+ cache/mask_*.png)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    paths = []
    for k, idx in enumerate(plan):
        scenes, files = [], {}   # arcname -> (local path, sha256)
        for i in idx:
            job = jobs[i]
            spec = json.loads(json.dumps(job["spec"]))
            for m in media_refs(spec):
                src = Path(m)
                if not src.is_file():
                    raise ValueError(f"scene {job['id']}: media missing: {m}")
                if m not in digests:
                    digests[m] = sha256(src)
                arc = f"media/{digests[m][:20]}{src.suffix.lower()}"   # content-named: shared media ships once
                files[arc] = (src, digests[m])
                _rewrite(spec, m, arc)
            if mask_cache:
                for f in _mask_files(job["spec"], Path(mask_cache)):
                    files[f"cache/{f.name}"] = (f, sha256(f))
            scenes.append({"id": job["id"], "key": job["key"], "spec": spec, "put_url": clip_url(job)})
        manifest = {arc: h for arc, (_, h) in files.items()}
        tar_path = out_dir / f"scenes-{k:02d}.tar"
        with tarfile.open(tar_path, "w") as tar:
            _add_bytes(tar, "scenes.json", json.dumps({"shard": k, "count": len(plan), "scenes": scenes},
                                                      indent=1).encode())
            _add_bytes(tar, "manifest.json", json.dumps(manifest, indent=1).encode())
            for arc, (src, _) in sorted(files.items()):
                tar.add(src, arcname=arc)
        paths.append(tar_path)
    return paths


def _rewrite(v, old: str, new: str):
    """Replace media path `old` by `new` everywhere in a (nested) spec, in place."""
    if isinstance(v, dict):
        for k, x in v.items():
            if x == old and k in ("photo", "video", "src"):
                v[k] = new
            else:
                _rewrite(x, old, new)
    elif isinstance(v, list):
        for x in v:
            _rewrite(x, old, new)


def scene_env(store, run: str, k: int, n: int, deadline_epoch: int) -> dict:
    out = f"runs/{run}/out"
    return {
        "RUN_ID": run, "SHARD_INDEX": str(k), "SHARD_COUNT": str(n), "DEADLINE_EPOCH": str(deadline_epoch),
        "WORKER_MODE": "scenes",
        "BUNDLE_URL": store.get_url(f"runs/{run}/scenes-{k:02d}.tar", 7200),
        "STATUS_PUT_URL": store.put_url(f"{out}/scenes-{k:02d}.status.json", 7200),
        "LOG_PUT_URL": store.put_url(f"{out}/scenes-{k:02d}.log", 7200),
    }


def clip_key(run: str, job: dict) -> str:
    return f"runs/{run}/clips/{job['id']}-{job['key']}.mp4"


def run_scene_burst(jobs: list[dict], machines: int, work_dir: Path, *, image: str = "",
                    min_cpu: int = 32, min_inet: float = 1000, disk: float = 40, deadline: int = 1800,
                    log: Callable[[str], None] = print, store=None, vast=None,
                    poll: float = 3.0) -> dict[str, Path]:
    """Render `jobs` ({id, key, spec, frames}) on up to `machines` rented CPU boxes.
    Returns {scene id: local clip path} for every clip that came back; the rest are the caller's."""
    from .launch import load_dotenv
    load_dotenv()
    t0 = time.time()
    say = lambda m: log(f"[burst {time.time() - t0:6.1f}s] {m}")  # noqa: E731
    image = image or os.environ.get("BURST_IMAGE", "")
    if not image:
        raise SystemExit("--burst needs BURST_IMAGE (the worker image, see worker/Dockerfile)")
    if store is None:
        from .storage import Store
        store = Store()
    vast = vast or Vast(os.environ.get("VAST_API_KEY", ""))
    run = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    work_dir = Path(work_dir) / run
    work_dir.mkdir(parents=True, exist_ok=True)

    from collage.render import DEFAULT_CACHE
    for j in jobs:
        j["mask_cached"] = bool(_mask_files(j["spec"], DEFAULT_CACHE))
    offers = pick_cpu_offers(vast.search_offers(min_cpu=min_cpu, min_inet_down=min_inet, disk_gb=disk,
                                                cpu_only=True), machines + 2, min_cpu)
    n = min(machines, len(offers), len(jobs))
    plan = plan_scene_shards(jobs, n)
    n = len(plan)
    say(f"run {run}: {len(jobs)} scenes -> {n} machines, est. cost per shard "
        f"{[round(sum(scene_cost(jobs[i]) for i in s)) for s in plan]} frame-units")
    for o in offers[:n]:
        say(f"offer {o['id']}: {o.get('cpu_cores_effective', 0):.0f} cpu · {o.get('gpu_name')} · "
            f"{o.get('inet_down', 0):.0f} Mbps · ${o.get('dph_total', 0):.3f}/h")

    tars = build_scene_bundles(jobs, plan, work_dir, lambda j: store.put_url(clip_key(run, j), 7200),
                               mask_cache=DEFAULT_CACHE)
    with ThreadPoolExecutor(max(1, n)) as ex:
        list(ex.map(lambda kv: store.upload(kv[1], f"runs/{run}/scenes-{kv[0]:02d}.tar"), enumerate(tars)))
    say(f"bundles uploaded ({sum(p.stat().st_size for p in tars) / 2**20:.1f} MB)")

    deadline_epoch = int(time.time()) + deadline
    created: dict[int, dict] = {}
    spares = offers[n:]
    finished: dict[int, dict] = {}         # shard -> status
    got: dict[str, Path] = {}
    stop = {"flag": False}
    old_term = signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))

    def rent(k: int, offer: dict) -> None:
        iid = vast.create(offer["id"], image, scene_env(store, run, k, n, deadline_epoch), disk,
                          f"{LABEL}-{run}-{k:02d}", ["/opt/app/worker/run.sh", "--scenes"])
        created[iid] = {"shard": k, "offer": offer, "running_at": None, "ended_at": None}
        say(f"shard {k}: instance {iid} ({offer.get('cpu_cores_effective', 0):.0f} cpu)")

    def collect(k: int, st: dict) -> None:
        for i in plan[k]:
            j = jobs[i]
            if j["id"] in (st.get("errors") or {}) or j["id"] not in (st.get("clips") or {}):
                continue
            dest = work_dir / f"{j['id']}-{j['key']}.mp4"
            try:
                store.download(clip_key(run, j), dest)
                got[j["id"]] = dest
            except Exception as e:  # noqa: BLE001 - a lost clip is rendered locally
                say(f"clip {j['id']}: download failed ({e})")

    try:
        with ThreadPoolExecutor(max(1, n)) as ex:
            list(ex.map(lambda k: rent(k, offers[k]), range(n)))
        retries = 0
        while not stop["flag"] and len(finished) < n:
            if time.time() > deadline_epoch:
                say("deadline reached, destroying everything")
                break
            for iid, info in list(created.items()):
                if info["ended_at"]:
                    continue
                k = info["shard"]
                st = store.read_json(f"runs/{run}/out/scenes-{k:02d}.status.json")
                inst = vast.show(iid) or {}
                status = inst.get("actual_status")
                if status == "running" and not info["running_at"]:
                    info["running_at"] = time.time()
                    say(f"shard {k}: running (billing from here)")
                if st:
                    vast.destroy(iid)
                    info["ended_at"] = time.time()
                    finished[k] = st
                    errs = st.get("errors") or {}
                    say(f"shard {k}: {len(st.get('clips') or {})} clips in {st.get('seconds', '?')}s"
                        + (f", {len(errs)} failed" if errs else "") + (f", error: {st['error']}" if st.get("error") else "")
                        + " -> destroyed")
                    collect(k, st)
                elif status in DEAD_STATES:
                    vast.destroy(iid)
                    info["ended_at"] = time.time()
                    if retries >= 2 or not spares:
                        say(f"shard {k}: machine {status}, no retries left (its scenes render locally)")
                        finished[k] = {"error": f"machine {status}"}
                        continue
                    retries += 1
                    say(f"shard {k}: machine {status}, retrying on a spare")
                    rent(k, spares.pop(0))
            if len(finished) < n:
                time.sleep(poll)
    finally:
        signal.signal(signal.SIGTERM, old_term)
        cost = teardown(vast, created, run, say)
    say(f"{len(got)}/{len(jobs)} clips back · machine time cost ≈ ${cost:.3f}")
    return got


def teardown(vast, created: dict, run: str, say: Callable[[str], None]) -> float:
    """Destroy every instance we created, sweep leftovers by label, return the billed cost."""
    for iid, info in created.items():
        try:
            vast.destroy(iid)
        except VastError as e:
            say(f"!! destroy {iid} failed: {e}")
        info["ended_at"] = info["ended_at"] or time.time()
    try:
        for i in vast.list():
            if str(i.get("label", "")).startswith(f"{LABEL}-{run}") and i.get("actual_status") not in (None, "destroyed"):
                vast.destroy(i["id"])
    except VastError as e:
        say(f"!! leftover sweep failed: {e} (run `python -m burst.reap`)")
    cost = sum(((info["ended_at"] - info["running_at"]) / 3600) * float(info["offer"].get("dph_total") or 0)
               for info in created.values() if info["running_at"])
    say(f"all {len(created)} instances destroyed")
    return cost
