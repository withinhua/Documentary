"""Harvest a topic into the local media library (run it in the background; re-runs are incremental).

    python -m library.ingest --topic "Coca-Cola" --era 1940-1990 \
        --entities "New Coke,Pepsi,Pepsi Challenge,Roberto Goizueta,Atlanta" \
        --requests projects/new-coke/requests.json \
        --from-candidates 'projects/*/footage/candidates.json' \
        --sources openverse,loc,archive,commons [--minutes 12] [--embed]

1. Generates many queries (topic × products/places/media words × decades, entities, and every
   query already written in the given requests files) and runs them through the footage/sources
   adapters over footage/net.Http (disk cache, per-host rate limits, circuit breaker).
2. Stores every item in library/data/library.db with licence class/flags, dims, dates, page URL.
3. Downloads a 512 px thumbnail (videos: up to 4 source-provided stills as keyframes; with
   --keyframes N also ffmpeg keyframes from preview files), measures sharpness/text/phash.
4. Imports existing candidates.json files (their thumbnails are already on disk).
5. Marks perceptual-hash duplicates across sources (keeps the better licence/resolution/source).
Then `python -m library.embed` (or --embed) embeds everything new.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

from footage import quality
from footage.core import LICENSE_SCORE, Candidate, Request
from footage.net import Http, HttpError
from footage.sources import registry

from . import db

VISUAL_TERMS = ["advertisement", "vintage advertisement", "poster", "bottle", "can", "sign", "logo", "truck",
                "delivery truck", "vending machine", "bottling plant", "factory", "store", "soda fountain",
                "billboard", "cooler", "carton", "magazine advertisement", "commercial", "headquarters",
                "newsreel", "display", "tray", "calendar", "girl", "Santa Claus"]
VIDEO_TERMS = ["commercial", "newsreel", "television advertisement", "film"]
SOURCE_PRIOR = {"commons": 1.0, "loc": 0.95, "nara": 0.95, "archive": 0.85, "smithsonian": 0.85,
                "openverse": 0.75, "local": 0.8, "youtube": 0.6, "pexels": 0.5, "pixabay": 0.45}
THUMB = 512


def decades(era: str) -> list[int]:
    years = [int(y) for y in re.findall(r"(1[5-9]\d\d|20\d\d)", era or "")]
    if not years:
        return []
    return list(range(min(years) // 10 * 10, max(years) // 10 * 10 + 1, 10))


def generate_queries(topic: str, era: str = "", entities: list[str] | None = None) -> dict[str, list[str]]:
    """{"photo": [...], "video": [...]} — broad on purpose; the index sorts it out later."""
    photo = [topic] + [f"{topic} {t}" for t in VISUAL_TERMS]
    photo += [f"{topic} {d}s" for d in decades(era)]
    photo += [f"{topic} advertisement {d}s" for d in decades(era)]
    video = [topic] + [f"{topic} {t}" for t in VIDEO_TERMS] + [f"{topic} {d}s" for d in decades(era)]
    for e in entities or []:
        e = e.strip()
        if not e:
            continue
        photo.append(e)
        video.append(e)
        if topic.lower() not in e.lower():
            photo.append(f"{e} {topic}")
    return {"photo": list(dict.fromkeys(photo)), "video": list(dict.fromkeys(video))}


def request_queries(paths: list[str]) -> dict[str, dict[str, list[str]]]:
    """source → kind → queries, from existing requests.json files (everything a writer already asked)."""
    out: dict[str, dict[str, list[str]]] = {}
    for pat in paths:
        for p in glob.glob(pat):
            data = json.loads(Path(p).read_text())
            data = data.get("requests", data) if isinstance(data, dict) else data
            for d in data:
                if d.get("kind") in (None, "none"):
                    continue
                r = Request.from_dict(d)
                kinds = ["photo", "video"] if r.kind == "any" else [r.kind]
                for src, qs in r.queries.items():
                    for k in kinds:
                        lst = out.setdefault(src, {}).setdefault(k, [])
                        lst += [q for q in qs if q and q not in lst]
                if r.subject:
                    for k in kinds:
                        lst = out.setdefault("*", {}).setdefault(k, [])
                        if r.subject not in lst:
                            lst.append(r.subject)
    return out


def thumb_rel(key: str, suffix: str = "") -> str:
    h = hashlib.sha1(key.encode()).hexdigest()
    return f"thumbs/{h[:2]}/{h}{suffix}.jpg"


def save_thumb(im, rel: str) -> None:
    p = db.data_dir() / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    im = im.copy()
    im.thumbnail((THUMB, THUMB))
    im.save(p, quality=88)


def analyse_store(con, key: str, im) -> None:
    q = quality.analyse(im)
    save_thumb(im, thumb_rel(key))
    con.execute("UPDATE items SET thumb_path=?, phash=?, sharpness=?, text_area=?, corner_text=?, mono=?, "
                "quality=?, status=CASE WHEN status='embedded' THEN status ELSE 'thumb' END WHERE key=?",
                (thumb_rel(key), q["phash"], q["sharpness"], q.get("text_area"), int(bool(q.get("corner_text"))),
                 int(bool(q.get("mono"))), quality.quality_score(q), key))


class Harvester:
    def __init__(self, con, http: Http, topic: str, deadline: float, per_query: int = 40,
                 keyframes: int = 0, log=print):
        self.con, self.http, self.topic, self.deadline = con, http, topic, deadline
        self.per_query, self.keyframes, self.log = per_query, keyframes, log
        self.cpu = asyncio.Semaphore(2)
        self.stats = {"searches": 0, "search_errors": 0, "found": 0, "new": 0, "thumbs": 0, "thumb_fail": 0}
        self.pending: list[asyncio.Task] = []
        self.host_fail: dict[str, int] = {}       # consecutive thumbnail failures per host

    def host_ok(self, url: str) -> bool:
        return self.host_fail.get(urlsplit(url).hostname or "", 0) < 6

    async def search(self, src, kind: str, query: str) -> bool:
        """Run one query; False when it failed."""
        if time.monotonic() > self.deadline:
            return True
        done = self.con.execute("SELECT n FROM harvest_log WHERE source=? AND kind=? AND query=? AND error IS NULL",
                                (src.name, kind, query)).fetchone()
        if done is not None:
            return True
        req = Request(id="lib", kind=kind, subject=query)
        err, cands = None, []
        try:
            cands = await src.search(self.http, req, query, kind, self.per_query)
        except Exception as e:  # noqa: BLE001 - one failed query never stops the harvest
            err = f"{type(e).__name__}: {str(e)[:160]}"
            self.stats["search_errors"] += 1
        self.stats["searches"] += 1
        self.con.execute("INSERT OR REPLACE INTO harvest_log VALUES (?,?,?,?,?,?)",
                         (src.name, kind, query, len(cands), err, time.time()))
        for c in cands:
            self.stats["found"] += 1
            if db.upsert(self.con, c, self.topic):
                self.stats["new"] += 1
                self.pending.append(asyncio.create_task(self.thumb(c)))
        self.con.commit()
        return err is None

    async def _img(self, url: str | None):
        if not url:
            return None
        host = urlsplit(url).hostname or ""
        if not self.host_ok(url):                    # a host refusing us (robot policy, 429s): stop asking
            return None
        try:
            data = await self.http.get_bytes(url)
            self.host_fail[host] = 0
        except (HttpError, OSError):
            self.host_fail[host] = self.host_fail.get(host, 0) + 1
            return None
        try:
            async with self.cpu:
                return await asyncio.to_thread(quality.load, data)
        except Exception:  # noqa: BLE001 - not an image
            return None

    async def thumb(self, c: Candidate) -> None:
        if time.monotonic() > self.deadline + 120:        # leave the rest for the next run
            return
        frames = []
        if c.kind == "video" and c.preview_frames:
            urls = c.preview_frames
            idx = sorted({max(0, min(len(urls) - 1, round((i + 0.5) * len(urls) / 4 - 0.5))) for i in range(4)})
            for j, i in enumerate(idx, 1):
                im = await self._img(urls[i])
                if im is not None:
                    t = (i + 0.5) / len(urls) * c.duration if c.duration else None
                    frames.append((j, t, im))
        im = await self._img(c.thumb_url)
        if im is None and c.kind == "photo" and c.full_url and c.full_url != c.thumb_url:
            im = await self._img(c.full_url)
        if im is None and frames:
            im = frames[len(frames) // 2][2]
        if im is None:
            self.con.execute("UPDATE items SET status='nothumb' WHERE key=?", (c.key,))
            self.stats["thumb_fail"] += 1
            return
        async with self.cpu:
            await asyncio.to_thread(self._store, c.key, im, frames)
        self.stats["thumbs"] += 1

    def _store(self, key, im, frames) -> None:
        analyse_store(self.con, key, im)
        for j, t, fim in frames:
            rel = thumb_rel(key, f"_f{j}")
            save_thumb(fim, rel)
            self.con.execute("INSERT OR REPLACE INTO frames VALUES (?,?,?,?)", (key, j, t, rel))

    async def drain(self) -> None:
        while self.pending:
            batch, self.pending = self.pending, []
            await asyncio.gather(*batch)
        self.con.commit()


async def harvest(con, topic: str, era: str, entities: list[str], sources: list[str], requests: list[str],
                  minutes: float, per_query: int, max_queries: int, log=print) -> dict:
    reg = registry()
    srcs = [reg[s] for s in sources if s in reg and reg[s].enabled()]
    gen = generate_queries(topic, era, entities)
    from_req = request_queries(requests)
    deadline = time.monotonic() + minutes * 60
    async with Http() as http:
        h = Harvester(con, http, topic, deadline, per_query, log=log)
        jobs = []
        for s in srcs:
            for kind in s.kinds:
                qs = list(from_req.get(s.name, {}).get(kind, []))            # writer's queries first
                qs += [q for q in from_req.get("*", {}).get(kind, []) if q not in qs]
                qs += [q for q in gen.get(kind, []) if q not in qs]
                qs = qs[:max_queries]
                jobs.append((s, kind, qs))

        async def run_source(s, kind, qs):
            fails = 0
            for q in qs:                       # sequential per source+kind; sources run in parallel
                fails = 0 if await h.search(s, kind, q) else fails + 1
                if fails >= 3:                 # blocked or rate-limited: leave it for the next run
                    log(f"[library] {s.name}/{kind}: 3 errors in a row, stopping this source for now")
                    return
        await asyncio.gather(*(run_source(*j) for j in jobs))
        await h.drain()
        h.stats["http"] = dict(http.stats)
    return h.stats


# ---------------------------------------------------------------- existing candidates.json

def import_candidates(con, pattern: str, topic: str = "", log=print) -> dict:
    from PIL import Image
    n_new = n_all = 0
    for p in sorted(glob.glob(pattern)):
        base = Path(p).resolve().parent
        try:
            doc = json.loads(Path(p).read_text())
        except ValueError:
            continue
        t = topic or base.parent.name
        for rid, r in (doc.get("requests") or {}).items():
            for e in r.get("candidates") or []:
                c = Candidate.from_dict(e)
                n_all += 1
                new = db.upsert(con, c, t)
                n_new += new
                row = con.execute("SELECT status FROM items WHERE key=?", (c.key,)).fetchone()
                if row["status"] not in ("new", "nothumb"):
                    continue
                tp = base / (e.get("thumb_path") or "")
                if not (e.get("thumb_path") and tp.is_file()):
                    continue
                try:
                    im = quality.load(str(tp))
                except Exception:  # noqa: BLE001
                    continue
                analyse_store(con, c.key, im)
                for j, f in enumerate(e.get("frames") or [], 1):
                    fp = base / f.get("path", "")
                    if fp.is_file():
                        rel = thumb_rel(c.key, f"_f{j}")
                        save_thumb(Image.open(fp).convert("RGB"), rel)
                        con.execute("INSERT OR REPLACE INTO frames VALUES (?,?,?,?)", (c.key, j, f.get("t"), rel))
        con.commit()
        log(f"[library] imported {p}")
    return {"candidates": n_all, "new": n_new}


# ---------------------------------------------------------------- dedupe

def preference(row) -> float:
    edge = max(row["width"] or 0, row["height"] or 0)
    return (LICENSE_SCORE.get(row["license"], 0.3) + SOURCE_PRIOR.get(row["source"], 0.6) +
            min(1.0, edge / 3000) + (row["quality"] or 0) * 0.5)


def dedupe(con, distance: int = 6) -> int:
    """Perceptual-hash duplicates across sources: point the worse copies at the best one."""
    rows = con.execute("SELECT key, source, license, width, height, quality, phash, kind FROM items "
                       "WHERE phash IS NOT NULL AND kind='photo'").fetchall()
    if not rows:
        return 0
    rows = sorted(rows, key=lambda r: -preference(r))
    h = np.array([int(r["phash"], 16) for r in rows], dtype=np.uint64)
    bits = np.unpackbits(h.view(np.uint8).reshape(-1, 8), axis=1)          # n × 64
    dup_of: dict[str, str] = {}
    alive = np.ones(len(rows), bool)
    for i in range(len(rows)):
        if not alive[i]:
            continue
        d = (bits[i + 1:] != bits[i]).sum(1)
        for j in np.nonzero((d <= distance) & alive[i + 1:])[0] + i + 1:
            alive[j] = False
            dup_of[rows[j]["key"]] = rows[i]["key"]
    con.execute("UPDATE items SET dup_of=NULL")
    con.executemany("UPDATE items SET dup_of=? WHERE key=?", [(v, k) for k, v in dup_of.items()])
    con.commit()
    return len(dup_of)


def summary(con) -> dict:
    q = lambda s: con.execute(s).fetchall()
    return {"items": q("SELECT COUNT(*) FROM items")[0][0],
            "by_source": {r[0]: r[1] for r in q("SELECT source, COUNT(*) FROM items GROUP BY source")},
            "by_status": {r[0]: r[1] for r in q("SELECT status, COUNT(*) FROM items GROUP BY status")},
            "by_kind": {r[0]: r[1] for r in q("SELECT kind, COUNT(*) FROM items GROUP BY kind")},
            "duplicates": q("SELECT COUNT(*) FROM items WHERE dup_of IS NOT NULL")[0][0],
            "frames": q("SELECT COUNT(*) FROM frames")[0][0]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topic", default="")
    ap.add_argument("--era", default="")
    ap.add_argument("--entities", default="", help="comma-separated people/products/places/events")
    ap.add_argument("--requests", action="append", default=[], help="requests.json glob whose queries to harvest too")
    ap.add_argument("--from-candidates", action="append", default=[], help="candidates.json glob to import")
    ap.add_argument("--sources", default="openverse,loc,archive,commons")
    ap.add_argument("--minutes", type=float, default=12, help="stop starting new searches after this long")
    ap.add_argument("--per-query", type=int, default=40)
    ap.add_argument("--max-queries", type=int, default=60, help="per source and kind")
    ap.add_argument("--embed", action="store_true", help="embed new items afterwards")
    a = ap.parse_args(argv)
    con = db.connect()
    t0 = time.time()
    report = {}
    for pat in a.from_candidates:
        report["import"] = import_candidates(con, pat, a.topic)
    t1 = time.time()
    if (a.topic or a.requests) and a.minutes > 0:
        report["harvest"] = asyncio.run(harvest(
            con, a.topic, a.era, [e for e in a.entities.split(",") if e.strip()], a.sources.split(","),
            a.requests, a.minutes, a.per_query, a.max_queries))
    t2 = time.time()
    report["duplicates_marked"] = dedupe(con)
    report["seconds"] = {"import": round(t1 - t0, 1), "harvest": round(t2 - t1, 1), "total": round(time.time() - t0, 1)}
    report["library"] = summary(con)
    print(json.dumps(report, indent=1))
    if a.embed:
        from . import embed
        embed.run(con)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
