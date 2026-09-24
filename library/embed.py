"""Embed every library thumbnail, video keyframe and title+description with the library model.

    python -m library.embed [--limit N] [--batch 32]

Incremental: only items without vectors for the current model are embedded. Vectors live in
the `vectors` table (float16); library.search builds its in-memory / FAISS index from them.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from PIL import Image

from . import db
from . import model as M


def item_text(row) -> str:
    t = (row["title"] or "").strip()
    d = (row["description"] or "").strip()
    return (t + (". " + d if d and d.lower() != t.lower() else ""))[:300] or row["key"]


def pending(con, name: str, limit: int | None = None) -> list:
    q = ("SELECT key, title, description, thumb_path FROM items WHERE thumb_path IS NOT NULL AND key NOT IN "
         "(SELECT key FROM vectors WHERE model=? AND slot=0) ORDER BY added")
    rows = con.execute(q, (name,)).fetchall()
    return rows[:limit] if limit else rows


def run(con=None, limit: int | None = None, batch: int = 32, log=print) -> dict:
    con = con or db.connect()
    name = M.model_name()
    rows = pending(con, name, limit)
    stats = {"model": name, "items": len(rows), "images": 0, "seconds_images": 0.0, "seconds_text": 0.0}
    if not rows:
        return stats
    t_load = time.time()
    m = M.get(name)
    stats["seconds_load"] = round(time.time() - t_load, 1)
    base = db.data_dir()
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        ims, slots = [], []
        for r in chunk:
            paths = [(0, r["thumb_path"])] + [(f["idx"], f["path"]) for f in
                                              con.execute("SELECT idx, path FROM frames WHERE key=? ORDER BY idx", (r["key"],))]
            for slot, p in paths:
                try:
                    ims.append(Image.open(base / p).convert("RGB"))
                    slots.append((r["key"], slot))
                except OSError:
                    continue
        t0 = time.time()
        v = m.images(ims, batch=batch) if ims else []
        stats["seconds_images"] += time.time() - t0
        stats["images"] += len(ims)
        t0 = time.time()
        tv = m.texts([item_text(r) for r in chunk])
        stats["seconds_text"] += time.time() - t0
        for (key, slot), vec in zip(slots, v):
            db.put_vec(con, key, slot, name, vec)
        for r, vec in zip(chunk, tv):
            db.put_vec(con, r["key"], -1, name, vec)
            if not any(k == r["key"] and s == 0 for k, s in slots):       # unreadable thumb: never retry
                db.put_vec(con, r["key"], 0, name, vec * 0)
        con.execute(f"UPDATE items SET status='embedded' WHERE key IN ({','.join('?' * len(chunk))})",
                    [r["key"] for r in chunk])
        con.commit()
        done = min(i + batch, len(rows))
        rate = stats["images"] / max(1e-6, stats["seconds_images"])
        log(f"[embed] {done}/{len(rows)} items · {stats['images']} images · {rate:.1f} img/s", file=sys.stderr)
    stats["images_per_sec"] = round(stats["images"] / max(1e-6, stats["seconds_images"]), 2)
    stats["texts_per_sec"] = round(len(rows) / max(1e-6, stats["seconds_text"]), 2)
    stats["seconds_images"] = round(stats["seconds_images"], 1)
    stats["seconds_text"] = round(stats["seconds_text"], 1)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--batch", type=int, default=32)
    a = ap.parse_args(argv)
    print(json.dumps(run(limit=a.limit, batch=a.batch), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
