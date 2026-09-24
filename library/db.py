"""The media library's SQLite store: one row per item (any source), its thumbnail and keyframes on
disk, quality numbers, perceptual hash, and embedding vectors (float16 BLOBs, per model).

Location: library/data/ (gitignored) or $LIBRARY_DIR. Everything a query needs is in here, so
searching never touches the network.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import numpy as np

from footage.core import Candidate, first_year

ROOT = Path(__file__).resolve().parent


def data_dir() -> Path:
    return Path(os.environ.get("LIBRARY_DIR") or ROOT / "data")


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  key TEXT PRIMARY KEY,            -- "<source>:<id>" (footage.core.Candidate.key)
  source TEXT, kind TEXT, title TEXT, description TEXT,
  thumb_url TEXT, full_url TEXT, page_url TEXT, preview_url TEXT,
  width INT, height INT, duration REAL, date TEXT, year INT,
  license TEXT, license_name TEXT, license_url TEXT, license_flags TEXT,
  author TEXT, credit TEXT, cand TEXT,  -- full Candidate JSON (what footage.fetch needs)
  topics TEXT DEFAULT '[]', queries TEXT DEFAULT '[]',
  thumb_path TEXT,                 -- 512 px JPEG, relative to data_dir()
  phash TEXT, sharpness REAL, text_area REAL, corner_text INT, mono INT, quality REAL,
  dup_of TEXT,                     -- key of the better copy (perceptual-hash duplicate)
  status TEXT DEFAULT 'new',       -- new | thumb | nothumb | embedded
  added REAL
);
CREATE INDEX IF NOT EXISTS items_status ON items(status);
CREATE INDEX IF NOT EXISTS items_source ON items(source);
CREATE TABLE IF NOT EXISTS frames (
  key TEXT, idx INT, t REAL, path TEXT, PRIMARY KEY (key, idx)
);
CREATE TABLE IF NOT EXISTS vectors (
  key TEXT, slot INT, model TEXT, vec BLOB,   -- slot: -1 = text (title+description), 0 = thumb, 1.. = frames
  PRIMARY KEY (key, slot, model)
);
CREATE TABLE IF NOT EXISTS prompt_cache (model TEXT, text TEXT, vec BLOB, PRIMARY KEY (model, text));
CREATE TABLE IF NOT EXISTS harvest_log (
  source TEXT, kind TEXT, query TEXT, n INT, error TEXT, at REAL, PRIMARY KEY (source, kind, query)
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    d = data_dir()
    path = path or d / "library.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def _merge_list(old: str | None, new: list[str]) -> str:
    cur = json.loads(old or "[]")
    for x in new:
        if x and x not in cur:
            cur.append(x)
    return json.dumps(cur[-50:])


def upsert(con: sqlite3.Connection, c: Candidate, topic: str = "", thumb_path: str | None = None) -> bool:
    """Insert or refresh an item. Returns True when the key is new."""
    row = con.execute("SELECT topics, queries, status FROM items WHERE key=?", (c.key,)).fetchone()
    d = c.to_dict()
    for k in ("n", "score", "scores", "quality", "low_res", "thumb_path", "frames", "source_label"):
        d.pop(k, None)
    year = first_year(c.date) or first_year(c.title)
    vals = dict(source=c.source, kind=c.kind, title=c.title, description=c.description, thumb_url=c.thumb_url,
                full_url=c.full_url, page_url=c.page_url, preview_url=c.preview_url, width=c.width,
                height=c.height, duration=c.duration, date=c.date, year=year, license=c.license,
                license_name=c.license_name, license_url=c.license_url,
                license_flags=json.dumps(c.license_flags), author=c.author, credit=c.credit,
                cand=json.dumps(d, ensure_ascii=False))
    if row is None:
        vals.update(key=c.key, topics=json.dumps([topic] if topic else []),
                    queries=json.dumps([c.query] if c.query else []), added=time.time(),
                    thumb_path=thumb_path, status="new")
        cols = ",".join(vals)
        con.execute(f"INSERT INTO items ({cols}) VALUES ({','.join('?' * len(vals))})", list(vals.values()))
        return True
    vals.update(topics=_merge_list(row["topics"], [topic]), queries=_merge_list(row["queries"], [c.query]))
    sets = ",".join(f"{k}=?" for k in vals)
    con.execute(f"UPDATE items SET {sets} WHERE key=?", [*vals.values(), c.key])
    return False


def candidate(row: sqlite3.Row) -> dict:
    """The stored item as a candidates.json-style dict (what footage.fetch consumes)."""
    d = json.loads(row["cand"] or "{}")
    d.setdefault("source", row["source"])
    d.setdefault("kind", row["kind"])
    return d


# ---------------------------------------------------------------- vectors

def put_vec(con, key: str, slot: int, model: str, v: np.ndarray) -> None:
    con.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)",
                (key, slot, model, np.asarray(v, dtype=np.float16).tobytes()))


def load_matrix(con, model: str, text: bool = False) -> tuple[list[str], np.ndarray, np.ndarray]:
    """(keys, slots, float32 matrix of unit vectors) for image slots (>=0) or text slot (-1)."""
    op = "=" if text else ">="
    rows = con.execute(f"SELECT key, slot, vec FROM vectors WHERE model=? AND slot {op} ? ORDER BY key, slot",
                       (model, -1 if text else 0)).fetchall()
    if not rows:
        return [], np.zeros(0, int), np.zeros((0, 1), np.float32)
    keys = [r[0] for r in rows]
    slots = np.array([r[1] for r in rows], dtype=np.int16)
    m = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float16).reshape(len(rows), -1).astype(np.float32)
    return keys, slots, m
