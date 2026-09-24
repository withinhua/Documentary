"""Wikimedia Commons: full-text search in the File namespace + imageinfo/videoinfo extmetadata.

The best source for historical photos of real people, products and places. Licence and author
come from extmetadata (LicenseShortName, LicenseUrl, Artist, Credit, DateTimeOriginal).
Docs: https://www.mediawiki.org/wiki/API:Search , API:Imageinfo , Extension:TimedMediaHandler
"""
from __future__ import annotations

from ..core import Candidate, Request, classify_license, strip_html
from . import Source

API = "https://commons.wikimedia.org/w/api.php"
META = ("LicenseShortName|LicenseUrl|License|Artist|Credit|ImageDescription|DateTimeOriginal|"
        "ObjectName|UsageTerms|Restrictions|Copyrighted")


class Commons(Source):
    name = "commons"
    kinds = ("photo", "video")
    hosts = ("commons.wikimedia.org", "upload.wikimedia.org")

    def params(self, query: str, kind: str, limit: int) -> dict:
        ft = {"photo": " filetype:bitmap", "video": " filetype:video"}.get(kind, "")
        prop = "videoinfo" if kind == "video" else "imageinfo"
        p = "vi" if kind == "video" else "ii"
        params = {"action": "query", "format": "json", "formatversion": "2", "generator": "search",
                  "gsrsearch": query + ft, "gsrnamespace": "6", "gsrlimit": str(min(limit, 50)),
                  "prop": prop, f"{p}prop": "url|size|mime|extmetadata|timestamp" + ("|derivatives" if p == "vi" else ""),
                  f"{p}urlwidth": "640", f"{p}extmetadatafilter": META, f"{p}extmetadatalanguage": "en"}
        return params

    async def search(self, http, req: Request, query: str, kind: str, limit: int) -> list[Candidate]:
        data = await http.get_json(API, self.params(query, kind, limit))
        return self.parse(data, query)

    def parse(self, data: dict, query: str = "") -> list[Candidate]:
        pages = (data.get("query") or {}).get("pages") or []
        if isinstance(pages, dict):                      # formatversion=1 shape
            pages = list(pages.values())
        out = []
        for pg in sorted(pages, key=lambda p: p.get("index", 0)):
            info = (pg.get("videoinfo") or pg.get("imageinfo") or [None])[0]
            if not info:
                continue
            mime = info.get("mime", "")
            kind = "video" if mime.startswith("video/") or (mime.startswith("application/ogg")) else "photo"
            if not (mime.startswith("image/") or kind == "video") or mime in ("image/svg+xml",):
                continue
            em = {k: (v or {}).get("value") for k, v in (info.get("extmetadata") or {}).items()}
            cls, lname, flags = classify_license(em.get("LicenseShortName") or em.get("License"),
                                                 em.get("LicenseUrl"))
            restr = strip_html(em.get("Restrictions"))
            if restr:
                flags = flags + [f"restriction:{r}" for r in restr.split("|") if r]
            author = strip_html(em.get("Artist"))[:120]
            title = pg.get("title", "").removeprefix("File:")
            preview = None
            if kind == "video":
                ders = [d for d in info.get("derivatives") or [] if d.get("src")]
                ders = sorted(ders, key=lambda d: int(d.get("height") or 9999))
                small = [d for d in ders if 240 <= int(d.get("height") or 0) <= 480] or ders
                preview = small[0]["src"] if small else info.get("url")
            c = Candidate(
                source="commons", id=str(pg.get("pageid") or title), kind=kind,
                title=strip_html(em.get("ObjectName")) or title.rsplit(".", 1)[0],
                description=strip_html(em.get("ImageDescription"))[:500],
                thumb_url=info.get("thumburl") or info.get("url"), full_url=info.get("url"),
                page_url=info.get("descriptionurl"), width=info.get("width"), height=info.get("height"),
                duration=info.get("duration"), license=cls, license_name=lname,
                license_url=em.get("LicenseUrl"), license_flags=flags, author=author,
                credit=strip_html(em.get("Credit"))[:160],
                date=strip_html(em.get("DateTimeOriginal"))[:40] or None, query=query,
                preview_url=preview, extra={"mime": mime})
            out.append(c)
        return out
