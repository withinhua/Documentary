"""Internet Archive: advancedsearch + per-item metadata. Images and movies (public-domain films,
newsreels, Prelinger, government films).

Video items expose `.thumbs/` still frames (a filmstrip without downloading the film) and small
derivatives ("512Kb MPEG4", "h.264") for keyframes and for fetching only the needed seconds.
Docs: https://archive.org/advancedsearch.php , https://archive.org/developers/md-read.html
"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from ..core import Candidate, Request, classify_license, strip_html
from ..net import HttpError
from . import Source

SEARCH = "https://archive.org/advancedsearch.php"
META = "https://archive.org/metadata/"
FIELDS = ["identifier", "title", "description", "licenseurl", "rights", "date", "year", "creator",
          "mediatype", "collection"]
PREVIEW_FORMATS = ("512Kb MPEG4", "MPEG4", "h.264", "h.264 IA", "Ogg Video", "WebM")
FULL_FORMATS = ("h.264", "MPEG4", "h.264 IA", "512Kb MPEG4", "Matroska", "QuickTime", "WebM", "Ogg Video")
IMAGE_FORMATS = ("JPEG", "PNG", "TIFF", "JPEG 2000")


def _secs(v) -> float | None:
    if v in (None, ""):
        return None
    s = str(v)
    if ":" in s:
        parts = [float(x) for x in s.split(":")]
        t = 0.0
        for p in parts:
            t = t * 60 + p
        return t
    try:
        return float(s)
    except ValueError:
        return None


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


class Archive(Source):
    name = "archive"
    kinds = ("photo", "video")
    hosts = ("archive.org", "ia800000.us.archive.org (any ia*.us.archive.org)")

    def params(self, query: str, kind: str, limit: int) -> dict:
        mt = {"photo": "image", "video": "movies"}.get(kind, "(image OR movies)")
        return {"q": f"({query}) AND mediatype:{mt}", "fl[]": FIELDS, "rows": str(min(limit, 50)),
                "page": "1", "output": "json"}

    async def search(self, http, req: Request, query: str, kind: str, limit: int) -> list[Candidate]:
        data = await http.get_json(SEARCH, self.params(query, kind, limit))
        docs = ((data.get("response") or {}).get("docs") or [])[:limit]

        async def one(doc):
            try:
                meta = await http.get_json(META + doc["identifier"])
            except HttpError:
                meta = {}
            return self.parse_item(doc, meta, query)

        items = await asyncio.gather(*(one(d) for d in docs if d.get("identifier")))
        return [c for c in items if c]

    def parse_item(self, doc: dict, meta: dict, query: str = "") -> Candidate | None:
        ident = doc["identifier"]
        md = {**doc, **(meta.get("metadata") or {})}
        files = meta.get("files") or []
        dl = f"https://archive.org/download/{quote(ident)}/"
        mediatype = md.get("mediatype") or doc.get("mediatype")
        kind = "video" if mediatype == "movies" else "photo"
        lic_url = md.get("licenseurl") or ""
        cls, lname, flags = classify_license(strip_html(md.get("rights"))[:120] if not lic_url else None, lic_url or None)
        c = Candidate(
            source="archive", id=ident, kind=kind, title=strip_html(md.get("title"))[:200],
            description=strip_html(md.get("description"))[:500],
            thumb_url=f"https://archive.org/services/img/{quote(ident)}", page_url=f"https://archive.org/details/{quote(ident)}",
            license=cls, license_name=lname, license_url=lic_url or None, license_flags=flags,
            author=strip_html(md.get("creator"))[:120], date=str(md.get("date") or md.get("year") or "")[:20] or None,
            query=query, extra={"collection": md.get("collection")})
        if kind == "video":
            vids = [f for f in files if f.get("format") in FULL_FORMATS]
            by_fmt = lambda fmts: next((f for fmt in fmts for f in vids if f.get("format") == fmt), None)
            prev, full = by_fmt(PREVIEW_FORMATS), by_fmt(FULL_FORMATS)
            if full:
                c.full_url = dl + quote(full["name"])
                c.width, c.height = _int(full.get("width")), _int(full.get("height"))
            if prev:
                c.preview_url = dl + quote(prev["name"])
            lengths = [_secs(f.get("length")) for f in vids if f.get("length")]
            c.duration = max([x for x in lengths if x] or [0]) or None
            thumbs = sorted(f["name"] for f in files
                            if f.get("format") == "Thumbnail" and ".thumbs/" in f.get("name", ""))
            if thumbs:
                c.preview_frames = [dl + quote(n) for n in thumbs]
            if not c.full_url and not files:
                return c if meta == {} else None
        else:
            imgs = [f for f in files if f.get("format") in IMAGE_FORMATS and f.get("source") == "original"] or \
                   [f for f in files if f.get("format") in IMAGE_FORMATS]
            if imgs:
                jpgs = [f for f in imgs if f.get("format") in ("JPEG", "PNG")] or imgs
                best = max(jpgs, key=lambda f: (_int(f.get("width")) or 0) * (_int(f.get("height")) or 0) or _int(f.get("size")) or 0)
                c.full_url = dl + quote(best["name"])
                c.width, c.height = _int(best.get("width")), _int(best.get("height"))
                if (_int(best.get("size")) or 0) < 3_000_000 and best.get("format") in ("JPEG", "PNG"):
                    c.thumb_url = c.full_url    # IA's own thumbs are ~180 px: too small to judge
            elif files:
                return None
        return c

