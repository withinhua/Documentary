"""Pexels and Pixabay: free stock photos and videos. Only active with a (free) API key:
PEXELS_API_KEY, PIXABAY_API_KEY. Good for generic b-roll (cities, offices, textures), rarely
for specific historical subjects, so they get a lower source prior in ranking.
Docs: https://www.pexels.com/api/documentation/ , https://pixabay.com/api/docs/
"""
from __future__ import annotations

import os

from ..core import Candidate, Request, strip_html
from . import Source


class Pexels(Source):
    name = "pexels"
    kinds = ("photo", "video")
    hosts = ("api.pexels.com", "images.pexels.com", "videos.pexels.com")
    env_key = "PEXELS_API_KEY"

    async def search(self, http, req: Request, query: str, kind: str, limit: int) -> list[Candidate]:
        hdr = {"Authorization": os.environ.get(self.env_key, "")}
        p = {"query": query, "per_page": str(min(limit, 80))}
        if kind == "video":
            return self.parse_videos(await http.get_json("https://api.pexels.com/videos/search", p, headers=hdr), query)
        return self.parse_photos(await http.get_json("https://api.pexels.com/v1/search", p, headers=hdr), query)

    def _lic(self):
        return dict(license="other", license_name="Pexels License", license_url="https://www.pexels.com/license/")

    def parse_photos(self, data: dict, query: str = "") -> list[Candidate]:
        out = []
        for p in data.get("photos") or []:
            src = p.get("src") or {}
            out.append(Candidate(
                source="pexels", id=str(p["id"]), kind="photo", title=strip_html(p.get("alt")) or f"Pexels {p['id']}",
                thumb_url=src.get("medium") or src.get("large"), full_url=src.get("original"), page_url=p.get("url"),
                width=p.get("width"), height=p.get("height"), author=p.get("photographer") or "",
                query=query, **self._lic()))
        return out

    def parse_videos(self, data: dict, query: str = "") -> list[Candidate]:
        out = []
        for v in data.get("videos") or []:
            files = [f for f in v.get("video_files") or [] if f.get("link") and (f.get("file_type") or "").endswith("mp4")]
            files.sort(key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))
            small = next((f for f in files if (f.get("height") or 0) >= 360), files[0] if files else None)
            big = next((f for f in reversed(files) if (f.get("height") or 0) <= 2160), files[-1] if files else None)
            slug = (v.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
            out.append(Candidate(
                source="pexels", id=str(v["id"]), kind="video", title=slug.replace("-", " ").strip() or f"Pexels video {v['id']}",
                thumb_url=v.get("image"), full_url=big and big["link"], page_url=v.get("url"),
                width=v.get("width"), height=v.get("height"), duration=v.get("duration"),
                author=(v.get("user") or {}).get("name", ""), query=query,
                preview_url=small and small["link"],
                preview_frames=[p["picture"] for p in v.get("video_pictures") or [] if p.get("picture")],
                **self._lic()))
        return out


class Pixabay(Source):
    name = "pixabay"
    kinds = ("photo", "video")
    hosts = ("pixabay.com", "cdn.pixabay.com")
    env_key = "PIXABAY_API_KEY"

    async def search(self, http, req: Request, query: str, kind: str, limit: int) -> list[Candidate]:
        p = {"key": os.environ.get(self.env_key, ""), "q": query[:100], "per_page": str(max(3, min(limit, 200))),
             "safesearch": "true"}
        if kind == "video":
            return self.parse_videos(await http.get_json("https://pixabay.com/api/videos/", p, ttl=86400), query)
        p["image_type"] = "photo"
        return self.parse_photos(await http.get_json("https://pixabay.com/api/", p, ttl=86400), query)

    def _lic(self):
        return dict(license="other", license_name="Pixabay Content License",
                    license_url="https://pixabay.com/service/license-summary/")

    def parse_photos(self, data: dict, query: str = "") -> list[Candidate]:
        return [Candidate(
            source="pixabay", id=str(h["id"]), kind="photo", title=h.get("tags", ""),
            thumb_url=h.get("webformatURL"), full_url=h.get("largeImageURL") or h.get("webformatURL"),
            page_url=h.get("pageURL"), width=h.get("imageWidth"), height=h.get("imageHeight"),
            author=h.get("user", ""), query=query, **self._lic()) for h in data.get("hits") or []]

    def parse_videos(self, data: dict, query: str = "") -> list[Candidate]:
        out = []
        for h in data.get("hits") or []:
            vids = h.get("videos") or {}
            big = next((vids[k] for k in ("large", "medium", "small") if (vids.get(k) or {}).get("url")), {})
            small = next((vids[k] for k in ("small", "tiny", "medium") if (vids.get(k) or {}).get("url")), {})
            out.append(Candidate(
                source="pixabay", id=str(h["id"]), kind="video", title=h.get("tags", ""),
                thumb_url=big.get("thumbnail") or small.get("thumbnail"), full_url=big.get("url"),
                page_url=h.get("pageURL"), width=big.get("width"), height=big.get("height"),
                duration=h.get("duration"), author=h.get("user", ""), query=query,
                preview_url=small.get("url"), **self._lic()))
        return out
