"""Smithsonian Open Access (api.si.edu via api.data.gov). Only CC0 media are kept.

Key: SMITHSONIAN_API_KEY or DATA_GOV_API_KEY (free at api.data.gov); falls back to DEMO_KEY
(about 30 requests/hour per IP, fine for testing, not for a full film).
Docs: https://edan.si.edu/openaccess/apidocs/
"""
from __future__ import annotations

import os

from ..core import Candidate, Request, strip_html
from . import Source

API = "https://api.si.edu/openaccess/api/v1.0/search"


def _free(ft: dict, key: str) -> str:
    vals = ft.get(key) or []
    return "; ".join(strip_html(v.get("content")) for v in vals if isinstance(v, dict))[:200]


class Smithsonian(Source):
    name = "smithsonian"
    kinds = ("photo",)
    hosts = ("api.si.edu", "ids.si.edu")

    def params(self, query: str, limit: int) -> dict:
        key = os.environ.get("SMITHSONIAN_API_KEY") or os.environ.get("DATA_GOV_API_KEY") or "DEMO_KEY"
        return {"q": f'{query} AND online_media_type:"Images"', "rows": str(min(limit, 100)), "api_key": key}

    async def search(self, http, req: Request, query: str, kind: str, limit: int) -> list[Candidate]:
        data = await http.get_json(API, self.params(query, limit))
        return self.parse(data, query)

    def parse(self, data: dict, query: str = "") -> list[Candidate]:
        out = []
        for row in (data.get("response") or {}).get("rows") or []:
            content = row.get("content") or {}
            dnr = content.get("descriptiveNonRepeating") or {}
            ft = content.get("freetext") or {}
            media = ((dnr.get("online_media") or {}).get("media")) or []
            for m in media:
                if m.get("type") != "Images" or (m.get("usage") or {}).get("access") != "CC0":
                    continue
                base = m.get("content") or ""
                full, w, h = base, None, None
                for r in m.get("resources") or []:
                    if "jpeg" in (r.get("label") or "").lower() and "high" in (r.get("label") or "").lower():
                        full, w, h = r.get("url") or full, r.get("width"), r.get("height")
                thumb = (base + "&max=640") if "deliveryService" in base else (m.get("thumbnail") or base)
                title = strip_html((dnr.get("title") or {}).get("content") or row.get("title"))
                out.append(Candidate(
                    source="smithsonian", id=str(m.get("idsId") or row.get("id")), kind="photo", title=title[:200],
                    description=(_free(ft, "notes") or _free(ft, "physicalDescription"))[:500],
                    thumb_url=thumb, full_url=full, page_url=dnr.get("record_link") or dnr.get("guid"),
                    width=int(w) if w else None, height=int(h) if h else None, license="cc0",
                    license_name="CC0 1.0", license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                    author=_free(ft, "name")[:120] or dnr.get("data_source", ""), date=_free(ft, "date")[:40] or None,
                    query=query, extra={"unit": dnr.get("unit_code")}))
                break                     # one image per object keeps the sheet varied
        return out
