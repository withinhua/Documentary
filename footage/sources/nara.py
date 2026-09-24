"""US National Archives catalog (catalog.archives.gov, API v2).

Federal records are public domain unless the record's use restriction says otherwise. The v2
API wants an `x-api-key` (free: email Catalog_API@nara.gov) → set NARA_API_KEY. Without a key we
try the catalog web app's proxy endpoint, which is undocumented and may refuse.
Docs: https://catalog.archives.gov/api/v2/api-docs , github.com/usnationalarchives/Catalog-API
"""
from __future__ import annotations

import os

from ..core import Candidate, Request, classify_license, strip_html
from . import Source

API = "https://catalog.archives.gov/api/v2/records/search"
PROXY = "https://catalog.archives.gov/proxy/records/search"


class NARA(Source):
    name = "nara"
    kinds = ("photo", "video")
    hosts = ("catalog.archives.gov", "s3.amazonaws.com (NARA-hosted media objects)")

    def params(self, query: str, limit: int) -> dict:
        return {"q": query, "limit": str(min(limit, 50)), "availableOnline": "true"}

    async def search(self, http, req: Request, query: str, kind: str, limit: int) -> list[Candidate]:
        key = os.environ.get("NARA_API_KEY")
        url, headers = (API, {"x-api-key": key}) if key else (PROXY, {})
        data = await http.get_json(url, self.params(query, limit), headers=headers)
        return self.parse(data, kind, query)

    def parse(self, data: dict, kind: str, query: str = "") -> list[Candidate]:
        hits = (((data.get("body") or data).get("hits") or {}).get("hits")) or []
        out = []
        for h in hits:
            src = h.get("_source") or {}
            rec = src.get("record") or src
            objs = rec.get("digitalObjects") or []
            want_video = kind == "video"
            pick = None
            for o in objs:
                t = (o.get("objectType") or "").lower()
                fn = (o.get("objectFilename") or o.get("objectUrl") or "").lower()
                is_vid = "video" in t or "moving" in t or fn.endswith((".mp4", ".mov", ".m4v", ".mpg"))
                is_img = "image" in t or fn.endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif"))
                if (want_video and is_vid) or (not want_video and is_img) or (kind == "any" and (is_vid or is_img)):
                    pick = o
                    break
            if not pick:
                continue
            ur = rec.get("useRestriction") or {}
            status = ur.get("status") if isinstance(ur, dict) else str(ur)
            if status and "unrestricted" in status.lower():
                cls, lname, flags = "public-domain", "Public domain (US federal record)", []
            elif status:
                cls, lname, flags = classify_license(status)
            else:
                cls, lname, flags = "public-domain", "Public domain (US federal record)", ["verify"]
            dates = rec.get("productionDates") or rec.get("inclusiveDates") or []
            date = None
            if isinstance(dates, list) and dates:
                d0 = dates[0]
                date = str(d0.get("year") or d0.get("logicalDate") or "") if isinstance(d0, dict) else str(d0)
            elif isinstance(dates, dict):
                date = str(dates.get("inclusiveStartDate", {}).get("year") or "") or None
            creators = rec.get("creators") or []
            author = strip_html(creators[0].get("heading") if creators and isinstance(creators[0], dict) else "")
            is_vid = want_video or (pick.get("objectType") or "").lower().find("video") >= 0
            naid = str(rec.get("naId") or h.get("_id") or "")
            url = pick.get("objectUrl")
            out.append(Candidate(
                source="nara", id=naid, kind="video" if is_vid else "photo", title=strip_html(rec.get("title"))[:200],
                description=strip_html(rec.get("scopeAndContentNote"))[:500],
                thumb_url=None if is_vid else url, full_url=url,
                page_url=f"https://catalog.archives.gov/id/{naid}" if naid else None,
                license=cls, license_name=lname, license_flags=flags, author=author or "U.S. National Archives",
                date=date or None, query=query, preview_url=url if is_vid else None,
                extra={"objectType": pick.get("objectType")}))
        return out
