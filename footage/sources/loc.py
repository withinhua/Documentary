"""Library of Congress (loc.gov JSON API): photos, prints, films.

Search with `fo=json`, then read each item (`<item id>?fo=json`) for its rights advisory and the
largest file. "No known restrictions on publication" → public domain (flag "verify").
loc.gov blocks clients that burst, so the host is rate-limited in net.HOST_POLICY.
Docs: https://www.loc.gov/apis/json-and-yaml/
"""
from __future__ import annotations

import asyncio
import re

from ..core import Candidate, Request, classify_license, strip_html
from ..net import HttpError
from . import Source

ENDPOINT = {"photo": "https://www.loc.gov/photos/", "video": "https://www.loc.gov/film-and-videos/"}


def _dims(url: str) -> tuple[int | None, int | None]:
    h = re.search(r"[#&]h=(\d+)", url or "")
    w = re.search(r"[#&]w=(\d+)", url or "")
    return (int(w.group(1)) if w else None, int(h.group(1)) if h else None)


def _https(u: str | None) -> str | None:
    if not u:
        return u
    u = u.split("#")[0]
    return "https:" + u if u.startswith("//") else u.replace("http://", "https://", 1)


class LOC(Source):
    name = "loc"
    kinds = ("photo", "video")
    hosts = ("www.loc.gov", "tile.loc.gov")

    def params(self, query: str, limit: int) -> dict:
        return {"q": query, "fo": "json", "c": str(min(limit, 50)), "at": "results,pagination"}

    async def search(self, http, req: Request, query: str, kind: str, limit: int) -> list[Candidate]:
        k = "video" if kind == "video" else "photo"
        data = await http.get_json(ENDPOINT[k], self.params(query, limit))
        results = [r for r in (data.get("results") or []) if not r.get("access_restricted")][:limit]

        async def one(r):
            item = {}
            if r.get("id"):
                try:
                    item = await http.get_json(_https(r["id"]), {"fo": "json", "at": "item,resources"})
                except HttpError:
                    item = {}
            return self.parse_result(r, item, k, query)

        out = await asyncio.gather(*(one(r) for r in results))
        return [c for c in out if c]

    def parse_result(self, r: dict, item: dict, kind: str, query: str = "") -> Candidate | None:
        imgs = [u for u in (r.get("image_url") or []) if u]
        if not imgs and kind == "photo":
            return None
        it = item.get("item") or {}
        rights = strip_html(it.get("rights_advisory") or r.get("rights_advisory") or it.get("rights") or r.get("rights"))
        cls, lname, flags = classify_license(rights[:200] if rights else None)
        thumb = _https(imgs[-1]) if imgs else None
        # largest image in the search result list is a fair full-res fallback
        best_w, best_h, full = None, None, thumb
        for u in imgs:
            w, h = _dims(u)
            if w and (best_w is None or w > best_w):
                best_w, best_h, full = w, h, _https(u)
        for u in imgs:                   # a mid-size (~ 500-1000 px) image makes the best thumb
            w, _ = _dims(u)
            if w and 300 <= w <= 1100:
                thumb = _https(u)
        preview = None
        for res in item.get("resources") or []:
            for group in res.get("files") or []:
                for f in group if isinstance(group, list) else [group]:
                    mt, w, h = f.get("mimetype") or "", f.get("width"), f.get("height")
                    if kind == "photo" and mt in ("image/jpeg", "image/png") and w and (best_w is None or int(w) > best_w):
                        best_w, best_h, full = int(w), int(h or 0) or None, _https(f.get("url"))
                    if kind == "video" and mt.startswith("video/"):
                        if not preview:
                            preview = _https(f.get("url"))
                        full = _https(f.get("url"))
            if res.get("video_stream") and kind == "video":
                full = full or _https(res["video_stream"])
        title = strip_html(r.get("title"))
        return Candidate(
            source="loc", id=(r.get("id") or "").rstrip("/").rsplit("/", 1)[-1] or title[:40], kind=kind,
            title=title[:200], description=strip_html(r.get("description"))[:500], thumb_url=thumb,
            full_url=full, page_url=_https(r.get("url") or r.get("id")), width=best_w, height=best_h,
            license=cls, license_name=lname if cls != "unknown" else (rights[:80] or "Unknown"),
            license_url="https://www.loc.gov/legal/" if cls == "public-domain" else None, license_flags=flags,
            author=strip_html((it.get("contributor_names") or r.get("contributor") or [""])[:1])[:120],
            date=str(r.get("date") or "")[:20] or None, query=query, preview_url=preview if kind == "video" else None,
            extra={"rights": rights[:300]})
