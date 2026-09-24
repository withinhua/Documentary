"""Burst render: rent the fastest GPU machines, render, save everything to storage, destroy the machines.

    python -m burst.launch projects/<slug> --shards 4 --deadline 900

1. Split the prepared job into N chapter shards and upload them to object storage (before renting).
2. Rent N machines at once (same GPU model, different hosts). Billing is per second, so N machines
   for T/N seconds cost about the same as one machine for T seconds, but finish N times sooner.
3. Each worker downloads its shard over the datacenter link, voices it, renders it, uploads the
   result and destroys itself. Worker 0 then joins all shards into final.mp4.
4. This launcher destroys every instance it created, whatever happens (success, error, Ctrl-C or
   deadline), then checks with vast.ai that none are left.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import bundle
from .vast import DEAD_STATES, Vast, VastError, pick_offers

LABEL = "docburst"


def log(t0: float, msg: str) -> None:
    print(f"[{time.time() - t0:7.1f}s] {msg}", flush=True)


def load_dotenv(path=".env") -> None:
    p = Path(path)
    if p.is_file():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def shard_env(store, run: str, k: int, n: int, deadline_epoch: int) -> dict:
    out = f"runs/{run}/out"
    env = {
        "RUN_ID": run, "SHARD_INDEX": str(k), "SHARD_COUNT": str(n),
        "DEADLINE_EPOCH": str(deadline_epoch),
        "BUNDLE_URL": store.get_url(f"runs/{run}/shard-{k:02d}.tar", 7200),
        "RESULT_PUT_URL": store.put_url(f"{out}/shard-{k:02d}.mp4", 7200),
        "PROJECT_PUT_URL": store.put_url(f"{out}/shard-{k:02d}.mlt", 7200),
        "STATUS_PUT_URL": store.put_url(f"{out}/shard-{k:02d}.status.json", 7200),
        "LOG_PUT_URL": store.put_url(f"{out}/shard-{k:02d}.log", 7200),
    }
    if k == 0:  # the leader joins everyone's output into final.mp4
        env["PEER_URLS"] = json.dumps([store.get_url(f"{out}/shard-{i:02d}.mp4", 7200) for i in range(1, n)])
        env["FINAL_PUT_URL"] = store.put_url(f"{out}/final.mp4", 7200)
        env["FINAL_STATUS_PUT_URL"] = store.put_url(f"{out}/final.status.json", 7200)
    return env


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_dir")
    ap.add_argument("--shards", type=int, default=4, help="machines to rent in parallel (default 4)")
    ap.add_argument("--gpus", default="", help="comma-separated GPU names in preference order")
    ap.add_argument("--min-cpu", type=int, default=32)
    ap.add_argument("--min-inet", type=float, default=2000, help="min download Mbps")
    ap.add_argument("--disk", type=float, default=80)
    ap.add_argument("--deadline", type=int, default=900, help="hard stop in seconds; everything is destroyed")
    ap.add_argument("--image", default=os.environ.get("BURST_IMAGE", ""))
    ap.add_argument("--dry-run", action="store_true", help="plan + search offers only, rent nothing")
    a = ap.parse_args(argv)
    load_dotenv()

    t0 = time.time()
    run = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    gpus = [g.strip().replace("_", " ") for g in a.gpus.split(",") if g.strip()] or None
    job = bundle.load_job(a.job_dir)
    plan = bundle.plan_shards(job["chapters"], a.shards)
    n = len(plan)
    log(t0, f"run {run}: {len(job['chapters'])} chapters -> {n} shards {plan}")

    vast = Vast(os.environ.get("VAST_API_KEY", ""))
    offers = pick_offers(vast.search_offers(gpus=gpus, min_cpu=a.min_cpu, min_inet_down=a.min_inet,
                                            disk_gb=a.disk), n + 2, gpus)  # +2 spares for retries
    for o in offers[:n]:
        log(t0, f"offer {o['id']}: {o.get('gpu_name')} · {o.get('cpu_cores_effective'):.0f} cpu · "
                f"{o.get('inet_down'):.0f} Mbps down · ${o.get('dph_total'):.3f}/h")
    if a.dry_run:
        return 0
    if not a.image:
        raise SystemExit("set BURST_IMAGE (see worker/Dockerfile)")

    from .storage import Store
    store = Store()
    work = Path(a.job_dir) / ".burst" / run
    tars = bundle.build_shards(a.job_dir, work, n)
    with ThreadPoolExecutor(n) as ex:
        list(ex.map(lambda kv: store.upload(kv[1], f"runs/{run}/shard-{kv[0]:02d}.tar"), enumerate(tars)))
    log(t0, f"bundles uploaded ({sum(p.stat().st_size for p in tars) / 2**20:.0f} MB)")

    deadline_epoch = int(time.time()) + a.deadline
    created: dict[int, dict] = {}          # instance id -> {shard, offer, running_at}
    spares = offers[n:]
    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))

    def rent(k: int, offer: dict) -> None:
        iid = vast.create(offer["id"], a.image, shard_env(store, run, k, n, deadline_epoch),
                          a.disk, f"{LABEL}-{run}-{k:02d}", ["/opt/app/worker/run.sh"])
        created[iid] = {"shard": k, "offer": offer, "running_at": None, "ended_at": None}
        log(t0, f"shard {k}: instance {iid} on {offer.get('gpu_name')}")

    ok = False
    try:
        with ThreadPoolExecutor(n) as ex:
            list(ex.map(lambda k: rent(k, offers[k]), range(n)))
        retries = 0
        while not stop["flag"]:
            if time.time() > deadline_epoch:
                log(t0, "deadline reached, destroying everything")
                break
            if store.read_json(f"runs/{run}/out/final.status.json"):
                ok = True
                break
            for iid, info in list(created.items()):
                if info["ended_at"]:
                    continue
                k = info["shard"]
                st = store.read_json(f"runs/{run}/out/shard-{k:02d}.status.json")
                inst = vast.show(iid) or {}
                status = inst.get("actual_status")
                if status == "running" and not info["running_at"]:
                    info["running_at"] = time.time()
                    log(t0, f"shard {k}: running (GPU billing from here)")
                if st and k != 0:          # a follower is finished: it self-destructs; make sure
                    vast.destroy(iid)
                    info["ended_at"] = time.time()
                    log(t0, f"shard {k}: done in {st.get('seconds', '?')}s -> destroyed")
                elif st and st.get("error"):
                    raise RuntimeError(f"shard {k} failed: {st['error']}")
                elif status in DEAD_STATES:
                    vast.destroy(iid)
                    info["ended_at"] = time.time()
                    if retries >= 2 or not spares:
                        raise RuntimeError(f"shard {k} machine died ({status}); no retries left")
                    retries += 1
                    log(t0, f"shard {k}: machine {status}, retrying on a spare")
                    rent(k, spares.pop(0))
            time.sleep(2)
    finally:
        for iid, info in created.items():
            try:
                vast.destroy(iid)
            except VastError as e:
                log(t0, f"!! destroy {iid} failed: {e}")
            info["ended_at"] = info["ended_at"] or time.time()
        leftover = [i for i in vast.list() if str(i.get("label", "")).startswith(f"{LABEL}-{run}")
                    and i.get("actual_status") not in (None, "destroyed")]
        for i in leftover:  # belt and braces: sweep by label too
            vast.destroy(i["id"])
        cost = sum(((info["ended_at"] - info["running_at"]) / 3600) * float(info["offer"].get("dph_total") or 0)
                   for info in created.values() if info["running_at"])
        log(t0, f"all {len(created)} instances destroyed · GPU time cost ≈ ${cost:.3f}")

    if ok:
        final = store.read_json(f"runs/{run}/out/final.status.json")
        log(t0, f"FINAL ready: s3://{store.bucket}/runs/{run}/out/final.mp4 ({final})")
        print(store.get_url(f"runs/{run}/out/final.mp4", 86400))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
