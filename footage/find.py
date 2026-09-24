"""Find candidate footage for every visual request and build contact sheets for review.

    python -m footage.find requests.json --out work/footage
    python -m footage.find requests.json --out work/footage --source commons --source loc
    python -m footage.find requests.json --out work/footage --source local:tests/footage_fixtures   # offline
    python -m footage.find requests.json --out work/footage --allow-youtube                        # opt-in

Writes <out>/candidates.json, <out>/contact/<id>.jpg (+ <id>/<n>.jpg zooms), <out>/thumbs/…,
and <out>/picks.template.json. Then follow footage/REVIEW.md and run footage.fetch.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import contact, frames, quality, relevance
from .core import (LICENSE_SCORE, SOURCE_LABELS, Candidate, Request, first_year, license_allowed, make_credit,
                   text_match)
from .net import Http, HttpError
from .sources import Source, registry
from .sources.local import LocalFolder

SOURCE_PRIOR = {"commons": 1.0, "loc": 0.95, "nara": 0.95, "archive": 0.85, "smithsonian": 0.85,
                "openverse": 0.75, "local": 0.8, "youtube": 0.6, "pexels": 0.5, "pixabay": 0.45}


@dataclass
class Options:
    per_request: int = 16
    per_query: int = 20
    max_queries: int = 3
    max_thumbs: int = 48
    allow_nc: bool = False
    allow_unknown: bool = True
    allow_fair_use: bool = False
    use_clip: bool = True
    frames_per_video: int = 4
    dedupe_distance: int = 6
    zooms: bool = True


@dataclass
class Item:
    c: Candidate
    image: object = None              # PIL image of the thumbnail (or first frame)
    thumb_bytes: bytes | None = None
    q: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)
    score: float = 0.0
    frames: list = field(default_factory=list)
    low_res: bool = False


def parse_sources(specs: list[str] | None, allow_youtube: bool) -> list[Source]:
    reg = registry()
    out: list[Source] = []
    if not specs:
        out = [s for s in reg.values() if s.default and s.enabled()]
    else:
        for spec in specs:
            if spec.startswith("local:"):
                out.append(LocalFolder(spec.split(":", 1)[1]))
            elif spec in reg:
                s = reg[spec]
                if not s.enabled():
                    print(f"[footage] {spec}: needs {s.env_key}; skipped", file=sys.stderr)
                    continue
                out.append(s)
            else:
                raise SystemExit(f"unknown source {spec!r}; choose from {', '.join(reg)} or local:<folder>")
    if allow_youtube and not any(s.name == "youtube" for s in out):
        out.append(reg["youtube"])
    if not allow_youtube:
        out = [s for s in out if s.name != "youtube"]
    return out


def search_kinds(req: Request, src: Source) -> list[str]:
    if req.kind == "any":
        return ["any"] if set(src.kinds) >= {"photo", "video"} else list(src.kinds)
    return [req.kind] if req.kind in src.kinds else []


async def gather(http: Http, req: Request, sources: list[Source], opt: Options) -> tuple[list[Candidate], dict]:
    jobs, labels = [], []
    for s in sources:
        for kind in search_kinds(req, s):
            qs = req.queries_for(s.name)[: opt.max_queries] if s.name != "local" else [req.subject or req.id]
            for q in qs:
                jobs.append(s.search(http, req, q, kind, opt.per_query))
                labels.append(s.name)
    results = await asyncio.gather(*jobs, return_exceptions=True)
    seen: dict[str, Candidate] = {}
    errors: dict[str, str] = {}
    counts: dict[str, int] = {}
    for name, res in zip(labels, results):
        if isinstance(res, Exception):
            errors[name] = f"{type(res).__name__}: {str(res)[:200]}"
            continue
        for c in res:
            if c.key not in seen:
                seen[c.key] = c
                counts[name] = counts.get(name, 0) + 1
    return list(seen.values()), {"errors": errors, "per_source": counts}


async def fetch_bytes(http: Http, url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        if url.startswith("file://"):
            return await asyncio.to_thread(Path(unquote(urlsplit(url).path)).read_bytes)
        return await http.get_bytes(url)
    except (HttpError, OSError) as e:
        print(f"[footage] thumb failed {url[:90]}: {e}", file=sys.stderr)
        return None


def era_score(req: Request, c: Candidate) -> float:
    rng = req.era_range()
    y = first_year(c.date) or first_year(c.title)
    if not rng or not y:
        return 0.0
    lo, hi = rng
    if lo - 2 <= y <= hi + 2:
        return 0.06
    if y < lo - 15 or y > hi + 15:
        return -0.08
    return 0.0


def rank_score(req: Request, it: Item, clip_on: bool) -> float:
    c = it.c
    lic = LICENSE_SCORE.get(c.license, 0.3)
    prior = SOURCE_PRIOR.get(c.source, 0.6)
    long_edge = max(c.width or 0, c.height or 0)
    res = min(1.0, long_edge / max(1, 2 * req.min_width)) if long_edge else 0.4
    txt = text_match(req, c)
    q = quality.quality_score(it.q) if it.q else 0.5
    clip = it.scores.get("clip")
    if clip_on and clip is not None:
        s = 0.45 * clip + 0.2 * txt + 0.12 * q + 0.08 * res + 0.08 * lic + 0.07 * prior
    else:
        s = 0.45 * txt + 0.18 * q + 0.12 * res + 0.12 * lic + 0.13 * prior
    s += era_score(req, c)
    it.scores.update({"text": txt, "quality": q, "res": round(res, 3), "license": lic, "prior": prior,
                      "era": era_score(req, c)})
    return round(s, 4)


def pick_diverse(items: list[Item], k: int) -> list[Item]:
    """Best first, but no single source takes more than ~60% of the sheet if others have stock."""
    cap = max(1, math.ceil(0.6 * k))
    chosen, per, rest = [], {}, []
    for it in items:
        if per.get(it.c.source, 0) < cap and len(chosen) < k:
            chosen.append(it)
            per[it.c.source] = per.get(it.c.source, 0) + 1
        else:
            rest.append(it)
    for it in rest:
        if len(chosen) >= k:
            break
        chosen.append(it)
    return sorted(chosen, key=lambda i: -i.score)


async def video_frames(http: Http, it: Item, work: Path, n: int) -> list[dict]:
    c = it.c
    if c.preview_frames:
        urls = c.preview_frames
        idx = [round((i + 0.5) * len(urls) / n - 0.5) for i in range(min(n, len(urls)))]
        idx = sorted(set(max(0, min(len(urls) - 1, i)) for i in idx))
        out = []
        for i in idx:
            data = await fetch_bytes(http, urls[i])
            if not data:
                continue
            p = work / f"pf_{i:03d}.jpg"
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                quality.load(data).save(p, quality=90)
            except Exception:
                continue
            t = (i + 0.5) / len(urls) * c.duration if c.duration else None
            out.append({"t": round(t, 1) if t is not None else None, "path": str(p)})
        if len(out) >= 2:
            return out
    src = c.preview_url or (c.full_url if c.source == "local" else None)
    if src:
        try:
            return await asyncio.to_thread(frames.keyframes, src, work, n, c.duration)
        except Exception as e:  # noqa: BLE001
            print(f"[footage] keyframes failed for {c.key}: {e}", file=sys.stderr)
    return []


class Finder:
    def __init__(self, http: Http, sources: list[Source], out: Path, opt: Options):
        self.http, self.sources, self.out, self.opt = http, sources, out, opt
        self.clip_lock = asyncio.Lock()
        self.cpu = asyncio.Semaphore(4)

    async def run_request(self, req: Request) -> dict:
        t0 = time.time()
        opt = self.opt
        cands, info = await gather(self.http, req, self.sources, opt)
        stats = {"found": len(cands), **info}
        cands = [c for c in cands if license_allowed(c, opt.allow_nc, opt.allow_unknown, opt.allow_fair_use)]
        stats["after_license"] = len(cands)
        if req.kind in ("photo", "video"):
            cands = [c for c in cands if c.kind == req.kind]
        items = []
        low = []
        for c in cands:
            long_edge = max(c.width or 0, c.height or 0)
            it = Item(c)
            if long_edge and long_edge < req.min_width:
                it.low_res = True
                low.append(it)
            else:
                items.append(it)
        if len(items) < opt.per_request // 2:          # thin pickings: show low-res too, flagged
            items += low
        stats["after_resolution"] = len(items)

        # pre-rank on metadata only, then look at the best few dozen
        for it in items:
            it.score = rank_score(req, it, clip_on=False)
        items.sort(key=lambda i: -i.score)
        items = items[: opt.max_thumbs]

        work = self.out / "thumbs" / req.id
        for stale in (work, self.out / "contact" / req.id):   # a re-run replaces this request's files
            shutil.rmtree(stale, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        scratch = lambda it: work / ("v_" + hashlib.sha1(it.c.key.encode()).hexdigest()[:12])

        async def load_thumb(i: int, it: Item):
            data = await fetch_bytes(self.http, it.c.thumb_url)
            if data is None and it.c.kind != "video" and it.c.full_url and it.c.full_url != it.c.thumb_url:
                data = await fetch_bytes(self.http, it.c.full_url)  # thumbnail service failed: use the original
            if data is None and it.c.kind == "video":
                it.frames = await video_frames(self.http, it, scratch(it), opt.frames_per_video)
                if it.frames:
                    data = Path(it.frames[len(it.frames) // 2]["path"]).read_bytes()
            if data is None:
                return
            try:
                async with self.cpu:
                    it.image = await asyncio.to_thread(quality.load, data)
                    it.q = await asyncio.to_thread(quality.analyse, it.image)
                it.thumb_bytes = data
            except Exception as e:  # noqa: BLE001
                print(f"[footage] bad image {it.c.key}: {e}", file=sys.stderr)

        await asyncio.gather(*(load_thumb(i, it) for i, it in enumerate(items)))
        items = [it for it in items if it.image is not None]
        stats["with_preview"] = len(items)
        blurry = [it for it in items if (it.q.get("sharpness") or 0) < quality.BLUR_REJECT and it.c.kind == "photo"]
        items = [it for it in items if it not in blurry]
        stats["rejected_blur"] = len(blurry)

        # perceptual-hash dedupe: keep the better copy (resolution, licence, source)
        for it in items:
            it.score = rank_score(req, it, clip_on=False)
        items.sort(key=lambda i: (-i.score, -(max(i.c.width or 0, i.c.height or 0))))
        kept: list[Item] = []
        for it in items:
            if any(quality.hamming(it.q["phash"], k.q["phash"]) <= opt.dedupe_distance for k in kept):
                continue
            kept.append(it)
        stats["rejected_duplicate"] = len(items) - len(kept)
        items = kept

        clip_on = False
        if opt.use_clip and items:
            async with self.clip_lock:
                texts = relevance.prompts(req.subject, req.must_show, req.era, req.kind)
                sims = await asyncio.to_thread(relevance.score, [it.image for it in items], texts)
            if sims is not None:
                clip_on = True
                for it, s in zip(items, sims):
                    it.scores["clip"] = s
        for it in items:
            it.score = rank_score(req, it, clip_on)
        items.sort(key=lambda i: -i.score)
        top = pick_diverse(items, opt.per_request)

        # filmstrips for the videos that made the sheet
        async def strip(i: int, it: Item):
            if it.c.kind == "video" and len(it.frames) < 2:
                it.frames = await video_frames(self.http, it, scratch(it), opt.frames_per_video)
        await asyncio.gather(*(strip(i, it) for i, it in enumerate(top)))

        entries = []
        for n, it in enumerate(top, 1):
            tp = work / f"{n:02d}.jpg"
            im = it.image.copy()
            im.thumbnail((1024, 1024))
            im.save(tp, quality=90)
            fr = []
            for j, f in enumerate(it.frames, 1):
                dst = work / f"{n:02d}_f{j}.jpg"
                if Path(f["path"]).exists():
                    shutil.copyfile(f["path"], dst)
                fr.append({"t": f.get("t"), "path": str(dst.relative_to(self.out))})
            d = it.c.to_dict()
            if not d.get("credit"):
                d["credit"] = make_credit(it.c)
            d.update(n=n, score=it.score, scores=it.scores, quality=it.q, low_res=it.low_res,
                     source_label=SOURCE_LABELS.get(it.c.source, it.c.source),
                     thumb_path=str(tp.relative_to(self.out)), frames=fr)
            entries.append(d)
        for p in work.glob("v_*"):                         # scratch frame folders
            shutil.rmtree(p, ignore_errors=True)

        # contact sheet (+ zooms) — paths inside entries are relative to out
        abs_entries = [{**e, "thumb_path": str(self.out / e["thumb_path"]),
                        "frames": [{**f, "path": str(self.out / f["path"])} for f in e["frames"]]} for e in entries]
        sheet = self.out / "contact" / f"{req.id}.jpg"
        async with self.cpu:
            await asyncio.to_thread(contact.contact_sheet, req.to_dict(), abs_entries, sheet)
            if self.opt.zooms:
                for e in abs_entries:
                    await asyncio.to_thread(contact.zoom, req.to_dict(), e, self.out / "contact" / req.id / f"{e['n']}.jpg")
        stats.update(shown=len(entries), clip=clip_on, seconds=round(time.time() - t0, 1))
        print(f"[footage] {req.id}: {stats['found']} found → {len(entries)} on sheet "
              f"({stats['seconds']}s; errors: {', '.join(stats['errors']) or 'none'})", file=sys.stderr)
        if ON_REQUEST_DONE:
            ON_REQUEST_DONE(req.id, stats)
        return {"request": req.to_dict(), "contact_sheet": str(sheet.relative_to(self.out)),
                "zoom_dir": f"contact/{req.id}/", "stats": stats, "candidates": entries}


async def run(requests: list[Request], sources: list[Source], out: Path, opt: Options,
              http: Http | None = None, parallel: int = 4) -> dict:
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    own = http is None
    http = http or Http()
    try:
        for s in sources:
            if isinstance(s, LocalFolder):
                s.set_request_ids([r.id for r in requests])
        f = Finder(http, sources, out, opt)
        sem = asyncio.Semaphore(parallel)

        async def one(r):
            async with sem:
                return r.id, await f.run_request(r)
        done = dict(await asyncio.gather(*(one(r) for r in requests)))
    finally:
        if own:
            await http.aclose()
    prev = {}
    if (out / "candidates.json").is_file():             # re-runs (e.g. requery) update in place
        try:
            prev = json.loads((out / "candidates.json").read_text()).get("requests") or {}
        except ValueError:
            prev = {}
    merged = {**prev, **{r.id: done[r.id] for r in requests}}
    doc = {"version": 1, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "sources": [s.name if not isinstance(s, LocalFolder) else f"local:{s.folder}" for s in sources],
           "clip": relevance.status() if opt.use_clip else "disabled",
           "policy": {"allow_nc": opt.allow_nc, "allow_unknown": opt.allow_unknown, "allow_fair_use": opt.allow_fair_use},
           "http": http.stats, "requests": merged}
    (out / "candidates.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    template = [{"id": rid, "pick": None, "alt": None, "crop": None, "in": None, "dur": None, "notes": ""}
                for rid in merged]
    (out / "picks.template.json").write_text(json.dumps(template, indent=1))
    return doc


def load_requests(path: Path) -> list[Request]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("requests") or []
    return [Request.from_dict(d) for d in data]


ON_REQUEST_DONE = None   # optional progress callback (request id, stats), set by main for the Studio


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("requests", type=Path, nargs="?")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--list-hosts", action="store_true", help="print the hostnames the network policy must allow")
    ap.add_argument("--source", action="append", help="source name or local:<folder>; repeatable (default: all keyless + keyed ones with keys)")
    ap.add_argument("--ids", help="only these request ids (comma-separated)")
    ap.add_argument("--allow-youtube", action="store_true", help="opt in to YouTube (copyrighted, fair-use risk)")
    ap.add_argument("--allow-nc", action="store_true", help="keep NC/ND licences (not for monetised videos)")
    ap.add_argument("--strict-license", action="store_true", help="drop candidates whose licence is unknown")
    ap.add_argument("--per-request", type=int, default=16)
    ap.add_argument("--per-query", type=int, default=20)
    ap.add_argument("--max-queries", type=int, default=3, help="queries used per source per request")
    ap.add_argument("--no-clip", action="store_true", help="skip the local CLIP relevance model")
    ap.add_argument("--no-zoom", action="store_true", help="skip per-candidate zoom images")
    ap.add_argument("--parallel", type=int, default=4, help="requests processed at once")
    ap.add_argument("--cache", type=Path, help="HTTP cache dir (default ~/.cache/documentary-footage or $FOOTAGE_CACHE)")
    ap.add_argument("--offline", action="store_true", help="use cached responses only; no network")
    a = ap.parse_args(argv)
    if a.list_hosts:
        for name, s in registry().items():
            print(f"{name:12s} {'(opt-in) ' if not s.default else ''}{'(needs ' + s.env_key + ') ' if s.env_key else ''}"
                  + " ".join(s.hosts))
        return 0
    if not (a.requests and a.out):
        ap.error("requests and --out are required")
    reqs = load_requests(a.requests)
    if a.ids:
        want = set(a.ids.split(","))
        reqs = [r for r in reqs if r.id in want]
    sources = parse_sources(a.source, a.allow_youtube)
    opt = Options(per_request=a.per_request, per_query=a.per_query, max_queries=a.max_queries,
                  allow_nc=a.allow_nc, allow_unknown=not a.strict_license, allow_fair_use=a.allow_youtube,
                  use_clip=not a.no_clip, zooms=not a.no_zoom)
    print(f"[footage] {len(reqs)} requests · sources: {', '.join(s.name for s in sources)}", file=sys.stderr)
    global ON_REQUEST_DONE
    from pipeline.live import tracker_for
    live = tracker_for(a.out)
    if live:
        done = {"n": 0, "found": 0}
        live.stage("footage", "running", f"Searching {len(reqs)} beats across {len(sources)} sources")

        def progress(rid, stats):
            done["n"] += 1
            done["found"] += stats.get("found", 0)
            live.stage("footage", "running", f"Searched {done['n']}/{len(reqs)} beats · {done['found']} candidates")
        ON_REQUEST_DONE = progress

    async def go():
        async with Http(cache_dir=a.cache, offline=a.offline) as http:
            return await run(reqs, sources, a.out, opt, http=http, parallel=a.parallel)
    doc = asyncio.run(go())
    if live:
        live.stage("footage", "running", f"{len(reqs)} beats searched · review sheets ready for the eyes")
    for rid, r in doc["requests"].items():
        print(f"{rid}\t{len(r['candidates'])} candidates\t{a.out / r['contact_sheet']}")
    print(f"→ review with footage/REVIEW.md, write picks.json (template: {a.out / 'picks.template.json'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
