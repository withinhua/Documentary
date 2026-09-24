"""Download the picked footage at full resolution and write the licence ledger.

    python -m footage.fetch picks.json --candidates work/footage/candidates.json --out work/footage/media

For each pick: `<out>/<request id>.<ext>` (the name pipeline.produce looks for). Videos with
`in`/`dur` fetch only those seconds (ffmpeg range-reads the remote file; yt-dlp
--download-sections for YouTube). Writes, next to <out>:
  sources.json   ledger keyed by "<out name>/<file>" → {source, license, credit, url, …} (job.json `sources`)
  picked.json    id → {path, candidate, crop, in, dur, notes}  (for the edit stage)
  requery.json   requests with no usable pick, with the reviewer's new queries: a valid requests
                 file, so `python -m footage.find <dir>/requery.json --out <dir>` searches again
"""
from __future__ import annotations

import argparse
import asyncio
import re
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

from .core import SOURCE_LABELS
from .net import Http, HttpError

Image.MAX_IMAGE_PIXELS = 400_000_000
KEEP_IMAGE = {".jpg", ".jpeg", ".png", ".webp"}
KEEP_VIDEO = {".mp4", ".mov", ".webm", ".mkv"}
MAX_EDGE = 3840  # 2× 1080p: room for push-ins, fast to render


def load_picks(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("picks") or [dict(v, id=k) for k, v in data.items()]
    return data


def ext_of(url: str | None, kind: str) -> str:
    suf = Path(unquote(urlsplit(url or "").path)).suffix.lower()
    if suf in KEEP_IMAGE | KEEP_VIDEO | {".tif", ".tiff", ".gif", ".bmp", ".ogv", ".ogg", ".mpg", ".mpeg", ".avi", ".m4v", ".jp2"}:
        return suf
    return ".jpg" if kind == "photo" else ".mp4"


def ffmpeg_clip(src: str, dest: Path, start: float | None, dur: float | None, timeout: int = 900) -> None:
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start:
        args += ["-ss", f"{start:.2f}"]
    args += ["-i", src]
    if dur:
        args += ["-t", f"{dur:.2f}"]
    args += ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest)]
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"ffmpeg: {r.stderr[-300:]}")


def normalise_image(path: Path) -> Path:
    """Keep jpg/png/webp as is (unless enormous); convert TIFF/GIF/BMP/JP2 → JPEG."""
    with Image.open(path) as im:
        big = max(im.size) > MAX_EDGE
        if path.suffix.lower() in KEEP_IMAGE and not big:
            return path
        im = im.convert("RGB")
        if big:
            im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
        dest = path.with_suffix(".jpg")
        im.save(dest, quality=93)
    if dest != path:
        path.unlink()
    return dest


def ytdlp_section(url: str, dest: Path, start: float | None, dur: float | None) -> Path:
    from .sources.youtube import ytdlp_cmd
    cmd = ytdlp_cmd()
    if not cmd:
        raise RuntimeError("yt-dlp not installed")
    args = [*cmd, "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b", "--merge-output-format", "mp4",
            "-o", str(dest.with_suffix(".%(ext)s")), "--no-playlist", "--no-warnings"]
    if start is not None or dur is not None:
        s = start or 0.0
        args += ["--download-sections", f"*{s:.2f}-{s + (dur or 10):.2f}", "--force-keyframes-at-cuts"]
    r = subprocess.run(args + [url], capture_output=True, text=True, timeout=1800)
    if r.returncode:
        raise RuntimeError(f"yt-dlp: {r.stderr[-300:]}")
    return next(dest.parent.glob(dest.stem + ".*"))


FLICKR_SIZED = re.compile(r"https://live\.staticflickr\.com/\d+/(\d+)_[0-9a-f]+_[a-z0-9]+\.(?:jpg|png)")
FLICKR_ORIG = re.compile(r"https://live\.staticflickr\.com/[^\"'\s]+?_(?:o|6k|5k|4k|3k|k|h)\.(?:jpg|png)")
LOC_SERVICE = re.compile(r"(https://tile\.loc\.gov/storage-services/)service(/pnp/.+/\w+?)[a-z]\.jpg$")


async def best_url(http: Http, cand: dict) -> list[str]:
    """Highest-resolution versions of a photo, best first (the finder's URL is often a 1024px copy)."""
    url = cand.get("full_url") or ""
    urls: list[str] = []
    m = FLICKR_SIZED.match(url)
    page = cand.get("page_url") or ""
    if m and "flickr.com/photos/" in page:
        try:  # Flickr's sizes page lists the original/largest files (different secret per size)
            html = await http.get_text(page.rstrip("/") + "/sizes/o/")
            found = FLICKR_ORIG.findall(html)
            rank = lambda u: next((i for i, s in enumerate(("_o.", "_6k.", "_5k.", "_4k.", "_3k.", "_k.", "_h."))
                                   if s in u), 9)
            urls += sorted(set(found), key=rank)
        except Exception:  # noqa: BLE001 - fall back to the known URL
            pass
    m = LOC_SERVICE.match(url)
    if m:  # LoC "v.jpg" (1024px) → the uncompressed master TIFF
        urls.append(f"{m.group(1)}master{m.group(2)}u.tif")
    return urls + [url]


async def fetch_one(http: Http, cand: dict, dest_stem: Path, start: float | None, dur: float | None) -> Path:
    kind, url, src = cand["kind"], cand.get("full_url"), cand.get("source")
    if not url:
        raise RuntimeError("candidate has no full_url")
    local = unquote(urlsplit(url).path) if url.startswith("file://") else None
    for old in dest_stem.parent.glob(dest_stem.name + ".*"):     # replace an earlier pick
        if old.suffix != ".part":
            old.unlink()
    if kind == "video":
        if src == "youtube":
            return await asyncio.to_thread(ytdlp_section, url, dest_stem, start, dur)
        suffix = ext_of(url, "video")
        if start is not None or dur is not None or suffix not in KEEP_VIDEO:
            dest = dest_stem.with_suffix(".mp4")
            src_file = local
            if not src_file:
                # Download with our client (proxy/CA aware, polite), then cut locally: ffmpeg's own
                # HTTP streaming is refused by some proxies and hosts.
                tmp = dest_stem.with_name(dest_stem.name + ".src" + suffix)
                await http.download(url, tmp, max_bytes=2_000_000_000)
                src_file = str(tmp)
            try:
                await asyncio.to_thread(ffmpeg_clip, src_file, dest, start, dur)
            finally:
                if not local:
                    Path(src_file).unlink(missing_ok=True)
            return dest
        dest = dest_stem.with_suffix(suffix)
        if local:
            await asyncio.to_thread(shutil.copyfile, local, dest)
        else:
            await http.download(url, dest)
        return dest
    if local:
        dest = dest_stem.with_suffix(ext_of(url, "photo"))
        await asyncio.to_thread(shutil.copyfile, local, dest)
        return await asyncio.to_thread(normalise_image, dest)
    last: Exception | None = None
    for u in await best_url(http, cand):
        dest = dest_stem.with_suffix(ext_of(u, "photo"))
        try:
            await http.download(u, dest, max_bytes=500_000_000)
            return await asyncio.to_thread(normalise_image, dest)
        except Exception as e:  # noqa: BLE001 - try the next (smaller) version
            last = e
            dest.unlink(missing_ok=True)
    raise RuntimeError(f"all versions failed: {last}")


def ledger_entry(cand: dict) -> dict:
    return {"source": SOURCE_LABELS.get(cand.get("source"), cand.get("source")),
            "license": cand.get("license_name") or cand.get("license"),
            "credit": cand.get("credit") or cand.get("author") or "",
            "url": cand.get("page_url") or cand.get("full_url"),
            "license_class": cand.get("license"), "license_url": cand.get("license_url"),
            "license_flags": cand.get("license_flags") or [], "title": cand.get("title"),
            "original": cand.get("full_url"), "candidate": f"{cand.get('source')}:{cand.get('id')}"}


async def run(picks: list[dict], candidates: dict, out: Path, http: Http, with_alt: bool = False,
              parallel: int = 3) -> dict:
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    root = out.parent
    ledger_path, picked_path, requery_path = root / "sources.json", root / "picked.json", root / "requery.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.is_file() else {}
    picked = json.loads(picked_path.read_text()) if picked_path.is_file() else {}
    requery, errors = [], {}
    reqs = candidates.get("requests") or {}
    sem = asyncio.Semaphore(parallel)

    def cand_for(rid: str, n) -> dict | None:
        for c in (reqs.get(rid) or {}).get("candidates") or []:
            if c.get("n") == n:
                return c
        return None

    async def one(p: dict):
        rid = str(p["id"])
        if rid not in reqs:
            errors[rid] = "no such request in candidates.json"
            return
        if p.get("pick") in (None, "", 0):
            r = reqs[rid]["request"]
            requery.append({**r, "queries": p.get("requery") or p.get("queries") or r.get("queries") or {},
                            "review_notes": p.get("notes", "")})
            return
        jobs = [("pick", p["pick"], out / rid)]
        if with_alt and p.get("alt"):
            jobs.append(("alt", p["alt"], out / "alts" / rid))
        src_id = str(p.get("from") or rid)   # "from": reuse a candidate found on another beat's sheet
        for role, n, stem in jobs:
            cand = cand_for(src_id, n)
            if not cand:
                errors[f"{rid}:{role}"] = f"no candidate #{n} on {src_id}"
                continue
            stem.parent.mkdir(parents=True, exist_ok=True)
            try:
                async with sem:
                    path = await fetch_one(http, cand, stem, p.get("in"), p.get("dur"))
            except (HttpError, RuntimeError, OSError, subprocess.SubprocessError) as e:
                errors[f"{rid}:{role}"] = f"{type(e).__name__}: {str(e)[:300]}"
                continue
            key = str(path.relative_to(root))
            for k in [k for k in ledger if Path(k).stem == rid and Path(k).parent == Path(key).parent and k != key]:
                ledger.pop(k)                       # a re-pick replaced a file with another extension
            ledger[key] = ledger_entry(cand)
            if role == "pick":
                picked[rid] = {"path": key, "candidate": n, "from": p.get("from"), "alt": p.get("alt"), "crop": p.get("crop"),
                               "in": p.get("in"), "dur": p.get("dur"), "notes": p.get("notes", ""),
                               "kind": cand.get("kind"), "width": cand.get("width"), "height": cand.get("height")}
            print(f"[fetch] {rid} #{n} → {key}", file=sys.stderr)

    await asyncio.gather(*(one(p) for p in picks))
    ledger_path.write_text(json.dumps(dict(sorted(ledger.items())), indent=1, ensure_ascii=False))
    picked_path.write_text(json.dumps(dict(sorted(picked.items())), indent=1, ensure_ascii=False))
    requery_path.write_text(json.dumps(requery, indent=1, ensure_ascii=False))
    return {"fetched": len(picked), "requery": [r["id"] for r in requery], "errors": errors,
            "ledger": str(ledger_path)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("picks", type=Path)
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True, help="media folder, e.g. <dir>/media")
    ap.add_argument("--with-alt", action="store_true", help="also fetch each alt into <out>/alts/")
    ap.add_argument("--parallel", type=int, default=3)
    a = ap.parse_args(argv)
    picks = load_picks(a.picks)
    cands = json.loads(a.candidates.read_text())

    async def go():
        async with Http() as http:
            return await run(picks, cands, a.out, http, a.with_alt, a.parallel)
    res = asyncio.run(go())
    print(json.dumps(res, indent=1))
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
