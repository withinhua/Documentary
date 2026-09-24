"""YouTube via yt-dlp search. OPT-IN ONLY (`--allow-youtube`): the footage is someone else's
copyright, so every candidate is flagged "fair-use" and must be short, commented-on use
(house rule, docs/03 §3). Filmstrip comes from YouTube's own stills (hq1/hq2/hq3.jpg at ~25/50/75%).
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys

from ..core import Candidate, Request, strip_html
from ..net import HttpError
from . import Source


def ytdlp_cmd() -> list[str] | None:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        return None


class YouTube(Source):
    name = "youtube"
    kinds = ("video",)
    hosts = ("www.youtube.com", "i.ytimg.com", "*.googlevideo.com")
    default = False

    async def search(self, http, req: Request, query: str, kind: str, limit: int) -> list[Candidate]:
        cache = http._cache_path("json", "ytsearch", {"q": query, "n": limit})
        if cache.is_file():
            data = json.loads(cache.read_text())
        else:
            if http.offline:
                raise HttpError("offline: youtube")
            cmd = ytdlp_cmd()
            if not cmd:
                raise HttpError("yt-dlp not installed (pip install yt-dlp)")
            p = await asyncio.create_subprocess_exec(
                *cmd, f"ytsearch{min(limit, 30)}:{query}", "--flat-playlist", "-J", "--no-warnings",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(p.communicate(), timeout=90)
            if p.returncode:
                raise HttpError(f"yt-dlp: {err.decode()[-200:]}")
            data = json.loads(out)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data))
        return self.parse(data, query)

    def parse(self, data: dict, query: str = "") -> list[Candidate]:
        out = []
        for e in data.get("entries") or []:
            vid = e.get("id")
            if not vid or e.get("live_status") in ("is_live", "is_upcoming"):
                continue
            base = f"https://i.ytimg.com/vi/{vid}/"
            out.append(Candidate(
                source="youtube", id=vid, kind="video", title=strip_html(e.get("title"))[:200],
                description=strip_html(e.get("description"))[:300], thumb_url=base + "hqdefault.jpg",
                full_url=f"https://www.youtube.com/watch?v={vid}", page_url=f"https://www.youtube.com/watch?v={vid}",
                duration=e.get("duration"), license="other", license_name="Copyrighted (fair-use risk)",
                license_flags=["fair-use"], author=e.get("channel") or e.get("uploader") or "", query=query,
                preview_frames=[base + f"hq{i}.jpg" for i in (1, 2, 3)],
                extra={"rights": "fair-use risk", "views": e.get("view_count")}))
        return out
