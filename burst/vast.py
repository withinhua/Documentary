"""Minimal vast.ai REST client: find the fastest offers, rent, watch, destroy.

Only the calls the burst launcher needs. Endpoints and payloads match the official `vastai` SDK
(console.vast.ai/api/v0). GPU billing starts when an instance reaches `running`; storage is
billed from creation until it is destroyed, so every code path must end in `destroy`.
"""
from __future__ import annotations

import time

import requests

API = "https://console.vast.ai/api/v0"

# GPUs worth renting for this workload: fast consumer/pro cards WITH NVENC encoders.
# Datacenter parts like A100/H100 have no NVENC, so they are slower here despite costing more.
DEFAULT_GPUS = ["RTX 5090", "RTX 4090", "RTX PRO 6000 WS", "L40S", "RTX 6000Ada"]

DEAD_STATES = {"exited", "unknown", "offline"}


class VastError(RuntimeError):
    pass


class Vast:
    def __init__(self, api_key: str, base: str = API, timeout: float = 20.0):
        if not api_key:
            raise VastError("VAST_API_KEY is not set")
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {api_key}"

    def _call(self, method: str, path: str, **kw) -> dict:
        for attempt in range(4):
            r = self.http.request(method, f"{self.base}{path}", timeout=self.timeout, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(0.5 * 2 ** attempt)
                continue
            if r.status_code >= 400:
                raise VastError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else {}
        raise VastError(f"{method} {path} kept failing (last HTTP {r.status_code})")

    # ── offers ──────────────────────────────────────────────────────────────────────────────
    def search_offers(self, gpus=None, min_cpu: int = 32, min_inet_down: float = 2000,
                      min_reliability: float = 0.98, disk_gb: float = 80, min_cuda: float = 12.4,
                      limit: int = 64) -> list[dict]:
        q = {
            "verified": {"eq": True}, "external": {"eq": False},
            "rentable": {"eq": True}, "rented": {"eq": False},
            "num_gpus": {"eq": 1},
            "gpu_name": {"in": list(gpus or DEFAULT_GPUS)},
            "cpu_cores_effective": {"gte": min_cpu},
            "inet_down": {"gte": min_inet_down},
            "reliability": {"gte": min_reliability},
            "cuda_max_good": {"gte": min_cuda},
            "disk_space": {"gte": disk_gb},
            "order": [["score", "desc"]],
            "type": "on-demand",
            "allocated_storage": disk_gb,
            "limit": limit,
        }
        return self._call("POST", "/bundles/", json=q).get("offers", [])

    # ── instances ───────────────────────────────────────────────────────────────────────────
    def create(self, offer_id: int, image: str, env: dict, disk_gb: float, label: str,
               args: list[str]) -> int:
        body = {
            "client_id": "me", "image": image, "env": env, "disk": disk_gb, "label": label,
            "runtype": "args", "args": args, "cancel_unavail": True,
        }
        res = self._call("PUT", f"/asks/{offer_id}/", json=body)
        if not res.get("success") or not res.get("new_contract"):
            raise VastError(f"create on offer {offer_id} failed: {res}")
        return int(res["new_contract"])

    def show(self, instance_id: int) -> dict | None:
        return self._call("GET", f"/instances/{instance_id}/", params={"owner": "me"}).get("instances")

    def list(self) -> list[dict]:
        return self._call("GET", "/instances/", params={"owner": "me"}).get("instances", [])

    def destroy(self, instance_id: int) -> None:
        try:
            self._call("DELETE", f"/instances/{instance_id}/", json={})
        except VastError as e:
            if "404" not in str(e):  # already gone is fine
                raise


def rank_offers(offers: list[dict], gpus=None) -> list[dict]:
    """Fastest first: GPU preference order, then download bandwidth, then CPU cores, then price.
    Speed beats price: at per-second billing a faster box also costs about the same per job."""
    pref = {g: i for i, g in enumerate(gpus or DEFAULT_GPUS)}
    return sorted(offers, key=lambda o: (
        pref.get(o.get("gpu_name"), 99),
        -float(o.get("inet_down") or 0),
        -float(o.get("cpu_cores_effective") or 0),
        float(o.get("dph_total") or 1e9),
    ))


def pick_offers(offers: list[dict], n: int, gpus=None) -> list[dict]:
    """N offers on N different machines, all the same GPU model so every shard encodes the same
    way (the shards are joined by stream copy, which needs identical encoder output)."""
    ranked = rank_offers(offers, gpus)
    by_gpu: dict[str, list[dict]] = {}
    for o in ranked:
        by_gpu.setdefault(o.get("gpu_name"), []).append(o)
    for gpu in [o.get("gpu_name") for o in ranked]:
        seen, chosen = set(), []
        for o in by_gpu[gpu]:
            if o.get("machine_id") in seen:
                continue
            seen.add(o.get("machine_id"))
            chosen.append(o)
            if len(chosen) == n:
                return chosen
    raise VastError(f"only found fewer than {n} matching machines; lower --shards or relax filters")
