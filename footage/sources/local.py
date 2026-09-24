"""Offline source: `--source local:<folder>`. A folder of images/videos acts as an archive.

- `<folder>/<request id>/…` files are candidates for that request only; otherwise every file in
  the folder (recursively, excluding other requests' subfolders) is a candidate for every request.
- Optional sidecar metadata: `<file>.json` or `<stem>.json` with any candidate fields, e.g.
  {"title": "...", "description": "...", "license": "CC BY 4.0", "license_url": "...",
   "author": "...", "date": "1985", "page_url": "https://..."}.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..core import Candidate, Request, classify_license
from . import Source

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi", ".mpg", ".ogv"}


def probe_video(path: Path) -> tuple[int | None, int | None, float | None]:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height:format=duration", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=30).stdout
        d = json.loads(out or "{}")
        s = (d.get("streams") or [{}])[0]
        dur = (d.get("format") or {}).get("duration")
        return s.get("width"), s.get("height"), float(dur) if dur else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None, None


class LocalFolder(Source):
    kinds = ("photo", "video")
    hosts = ()

    def __init__(self, folder: str | Path):
        self.folder = Path(folder).expanduser().resolve()
        self.name = "local"
        self._cache: dict[str, list[Candidate]] = {}

    def files_for(self, req: Request) -> list[Path]:
        own = self.folder / req.id
        root = own if own.is_dir() else self.folder
        exts = IMAGE_EXT | VIDEO_EXT
        files = []
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            rel = p.relative_to(root).parts
            if root == self.folder and len(rel) > 1 and self._is_request_dir(rel[0]):
                continue
            files.append(p)
        return files

    def _is_request_dir(self, name: str) -> bool:
        return getattr(self, "_request_ids", None) is not None and name in self._request_ids

    def set_request_ids(self, ids):
        self._request_ids = set(ids)

    async def search(self, http, req: Request, query: str, kind: str, limit: int) -> list[Candidate]:
        if req.id not in self._cache:
            self._cache[req.id] = [self.candidate(p) for p in self.files_for(req)]
        return [c for c in self._cache[req.id] if kind in ("any", c.kind)]

    def candidate(self, p: Path) -> Candidate:
        meta = {}
        for side in (p.with_name(p.name + ".json"), p.with_suffix(".json")):
            if side.is_file():
                try:
                    meta = json.loads(side.read_text())
                except ValueError:
                    meta = {}
                break
        kind = "video" if p.suffix.lower() in VIDEO_EXT else "photo"
        w = h = dur = None
        if kind == "video":
            w, h, dur = probe_video(p)
        else:
            try:
                from PIL import Image
                with Image.open(p) as im:
                    w, h = im.size
            except Exception:
                pass
        cls, lname, flags = classify_license(meta.get("license"), meta.get("license_url"))
        uri = p.as_uri()
        return Candidate(
            source="local", id=str(p.relative_to(self.folder)), kind=kind,
            title=meta.get("title") or p.stem.replace("_", " ").replace("-", " "),
            description=meta.get("description", ""), thumb_url=uri if kind == "photo" else None, full_url=uri,
            page_url=meta.get("page_url"), width=meta.get("width") or w, height=meta.get("height") or h,
            duration=meta.get("duration") or dur, license=cls, license_name=lname,
            license_url=meta.get("license_url"), license_flags=flags, author=meta.get("author", ""),
            credit=meta.get("credit", ""), date=str(meta["date"]) if meta.get("date") else None,
            preview_url=uri if kind == "video" else None, extra={"path": str(p)})
