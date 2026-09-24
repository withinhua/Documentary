"""Job bundles: everything a GPU worker needs, prepared in advance so the rented machine only executes.

A job directory (made by the prep stage: script, sentence-anchored edit plan, downloaded and trimmed
media) is split into shards of whole chapters, balanced by word count. Each shard becomes one
uncompressed tar (media is already compressed, and tar extracts at disk speed) holding job.json
reduced to its chapters plus only the media those chapters use.
"""
from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path


def load_job(job_dir) -> dict:
    job = json.loads((Path(job_dir) / "job.json").read_text())
    validate(job, Path(job_dir))
    return job


def validate(job: dict, root: Path) -> None:
    if not job.get("chapters"):
        raise ValueError("job.json has no chapters")
    missing = [s for s in media_of(job["chapters"]) if not (root / s).is_file()]
    if missing:
        raise ValueError(f"media missing from the job folder: {missing[:5]}{' …' if len(missing) > 5 else ''}")


def media_of(chapters: list[dict]) -> list[str]:
    out = []
    for ch in chapters:
        for b in ch.get("beats", []):
            src = (b.get("visual") or {}).get("src")
            if src and src not in out:
                out.append(src)
    for ch in chapters:
        if ch.get("music") and ch["music"] not in out:
            out.append(ch["music"])
    return out


def words(ch: dict) -> int:
    return sum(len(b.get("text", "").split()) for b in ch.get("beats", []))


def plan_shards(chapters: list[dict], n: int) -> list[list[int]]:
    """Contiguous runs of chapters with near-equal word counts (contiguous so shards concatenate in order)."""
    n = max(1, min(n, len(chapters)))
    total = sum(words(c) for c in chapters)
    shards, cur, acc = [], [], 0
    for i, ch in enumerate(chapters):
        cur.append(i)
        acc += words(ch)
        left_ch, left_sh = len(chapters) - i - 1, n - len(shards) - 1
        if left_sh > 0 and (acc >= total * (len(shards) + 1) / n or left_ch == left_sh):
            shards.append(cur)
            cur = []
    if cur:
        shards.append(cur)
    return shards


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build_shards(job_dir, out_dir, n: int) -> list[Path]:
    job_dir, out_dir = Path(job_dir), Path(out_dir)
    job = load_job(job_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = plan_shards(job["chapters"], n)
    paths = []
    for k, idx in enumerate(plan):
        chapters = [job["chapters"][i] for i in idx]
        shard_job = {**job, "chapters": chapters, "shard": {"index": k, "count": len(plan), "chapters": idx}}
        media = media_of(chapters)
        manifest = {m: sha256(job_dir / m) for m in media}
        tar_path = out_dir / f"shard-{k:02d}.tar"
        with tarfile.open(tar_path, "w") as tar:
            _add_bytes(tar, "job.json", json.dumps(shard_job, indent=1).encode())
            _add_bytes(tar, "manifest.json", json.dumps(manifest, indent=1).encode())
            for m in media:
                tar.add(job_dir / m, arcname=m)
        paths.append(tar_path)
    return paths


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    import io
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def verify_extracted(root: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text())
    bad = [m for m, h in manifest.items() if sha256(root / m) != h]
    if bad:
        raise ValueError(f"corrupt media after transfer: {bad[:5]}")
