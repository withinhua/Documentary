"""Openverse: CC-licensed and public-domain images from Flickr, museums, Wikimedia and more.

`license_type=commercial,modification` asks the API itself to drop NC and ND works.
Anonymous access is rate-limited; set OPENVERSE_CLIENT_ID/OPENVERSE_CLIENT_SECRET for a token.
Docs: https://api.openverse.org/v1/
"""
from __future__ import annotations

import os

from ..core import Candidate, Request, classify_license, strip_html
from . import Source

API = "https://api.openverse.org/v1/images/"


class Openverse(Source):
    name = "openverse"
    kinds = ("photo",)
    hosts = ("api.openverse.org", "live.staticflickr.com", "*.staticflickr.com")   # + each provider's image host for full-res
    _token: str | None = None

    async def _auth(self, http) -> dict:
        cid, sec = os.environ.get("OPENVERSE_CLIENT_ID"), os.environ.get("OPENVERSE_CLIENT_SECRET")
        if not (cid and sec) or http.offline:
            return {}
        if self._token is None:
            try:
                r = await http._client.post("https://api.openverse.org/v1/auth_tokens/token/",
                                            data={"client_id": cid, "client_secret": sec,
                                                  "grant_type": "client_credentials"})
                self._token = r.json().get("access_token", "")
            except Exception:
                self._token = ""
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def params(self, query: str, limit: int, allow_nc: bool = False) -> dict:
        p = {"q": query, "page_size": str(min(limit, 20)), "mature": "false"}
        if not allow_nc:
            p["license_type"] = "commercial,modification"
        return p

    async def search(self, http, req: Request, query: str, kind: str, limit: int) -> list[Candidate]:
        data = await http.get_json(API, self.params(query, limit), headers=await self._auth(http))
        return self.parse(data, query)

    def parse(self, data: dict, query: str = "") -> list[Candidate]:
        out = []
        for r in data.get("results") or []:
            code = (r.get("license") or "").lower()
            name = {"pdm": "Public Domain Mark", "cc0": "CC0"}.get(code, f"CC {code.upper()} {r.get('license_version') or ''}")
            cls, lname, flags = classify_license(name.strip(), r.get("license_url"))
            provider = r.get("provider") or r.get("source") or ""
            thumb = r.get("thumbnail") or (f"{API}{r['id']}/thumb/" if r.get("id") else r.get("url"))
            out.append(Candidate(
                source="openverse", id=str(r.get("id")), kind="photo", title=strip_html(r.get("title")),
                description=" ".join(t.get("name", "") for t in (r.get("tags") or [])[:12] if isinstance(t, dict)),
                thumb_url=thumb, full_url=r.get("url"), page_url=r.get("foreign_landing_url"),
                width=r.get("width"), height=r.get("height"), license=cls, license_name=lname,
                license_url=r.get("license_url"), license_flags=flags, author=strip_html(r.get("creator")),
                credit=strip_html(r.get("attribution"))[:200], query=query,
                extra={"provider": provider, "filetype": r.get("filetype")}))
        return out
