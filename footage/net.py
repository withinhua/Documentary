"""Polite async HTTP: per-host rate limits, retries with backoff, on-disk response cache."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

USER_AGENT = ("DocumentaryFootageFinder/0.1 (https://github.com/; documentary research; "
              "contact via repository) httpx")

# Minimum seconds between request starts, and max in-flight requests, per host.
HOST_POLICY: dict[str, tuple[float, int]] = {
    "commons.wikimedia.org": (0.1, 4),
    "upload.wikimedia.org": (0.05, 6),
    "api.openverse.org": (0.5, 2),
    "archive.org": (0.2, 4),
    "www.loc.gov": (0.6, 2),          # loc.gov bans bursts; keep it slow
    "tile.loc.gov": (0.2, 4),
    "catalog.archives.gov": (0.3, 2),
    "api.si.edu": (0.3, 2),
    "api.pexels.com": (0.3, 2),
    "pixabay.com": (0.7, 2),
}
DEFAULT_POLICY = (0.1, 4)
SECRET_PARAMS = {"key", "api_key", "apikey", "token", "client_secret"}


def default_cache_dir() -> Path:
    return Path(os.environ.get("FOOTAGE_CACHE") or Path.home() / ".cache" / "documentary-footage")


def _ssl_context():
    """Honour a corporate/sandbox CA bundle (SSL_CERT_FILE / REQUESTS_CA_BUNDLE) when set."""
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca and os.path.isfile(ca):
        return ssl.create_default_context(cafile=ca)
    return True


class HttpError(Exception):
    pass


class Http:
    """One shared client. `transport` lets tests plug in httpx.MockTransport."""

    def __init__(self, cache_dir: Path | None = None, ttl: float = 7 * 86400, offline: bool = False,
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30.0):
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.ttl = ttl
        self.offline = offline
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout, follow_redirects=True, transport=transport,
            verify=_ssl_context())
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._next: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tripped: dict[str, float] = {}   # host -> monotonic time until which we skip it
        self.stats = {"requests": 0, "cache_hits": 0, "errors": 0, "tripped": []}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self):
        await self._client.aclose()

    # -- rate limiting
    async def _slot(self, host: str):
        interval, conc = HOST_POLICY.get(host, DEFAULT_POLICY)
        sem = self._sems.setdefault(host, asyncio.Semaphore(conc))
        await sem.acquire()
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            wait = self._next.get(host, 0) - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next[host] = max(now, self._next.get(host, 0)) + interval
        return sem

    def _cache_path(self, kind: str, url: str, params: dict | None, headers: dict | None = None) -> Path:
        clean = {k: v for k, v in (params or {}).items() if k.lower() not in SECRET_PARAMS}
        key = url + "?" + urlencode(sorted((k, str(v)) for k, v in clean.items()), doseq=True)
        h = hashlib.sha1(key.encode()).hexdigest()
        return self.cache_dir / kind / h[:2] / h

    async def _request(self, url: str, params: dict | None, headers: dict | None) -> httpx.Response:
        if self.offline:
            raise HttpError(f"offline: {url}")
        host = urlsplit(url).hostname or ""
        # Circuit breaker: a host that is rate-limiting us is skipped for a while instead of
        # making every request wait through retries (speed matters more than one source).
        if self._tripped.get(host, 0) > time.monotonic():
            raise HttpError(f"{host} is rate-limiting us; skipped for now")
        last: Exception | None = None
        for attempt in range(4):
            sem = await self._slot(host)
            try:
                self.stats["requests"] += 1
                r = await self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as e:
                last = e
                r = None
            finally:
                sem.release()
            if r is not None:
                if r.status_code < 400:
                    return r
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise HttpError(f"HTTP {r.status_code} for {r.request.url}")
                last = HttpError(f"HTTP {r.status_code} for {r.request.url}")
                ra = r.headers.get("retry-after", "")
                delay = float(ra) if ra.isdigit() else 1.5 * 2 ** attempt
                if r.status_code == 429 and (delay > 5 or attempt >= 1):
                    self._trip(host)
                    raise last
            else:
                delay = 1.0 * 2 ** attempt
            if attempt < 3:
                await asyncio.sleep(min(delay, 20))
        self.stats["errors"] += 1
        raise HttpError(str(last))

    def _trip(self, host: str, seconds: float = 300.0) -> None:
        if self._tripped.get(host, 0) <= time.monotonic():
            self.stats["tripped"].append(host)
        self._tripped[host] = time.monotonic() + seconds
        self.stats["errors"] += 1

    async def get_json(self, url: str, params: dict | None = None, headers: dict | None = None,
                       ttl: float | None = None) -> Any:
        path = self._cache_path("json", url, params)
        ttl = self.ttl if ttl is None else ttl
        if path.is_file() and (self.offline or time.time() - path.stat().st_mtime < ttl):
            try:
                self.stats["cache_hits"] += 1
                return json.loads(path.read_text())
            except ValueError:
                pass
        r = await self._request(url, params, headers)
        try:
            data = r.json()
        except ValueError as e:
            raise HttpError(f"not JSON from {url}: {r.text[:120]!r}") from e
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
        return data

    async def get_bytes(self, url: str, headers: dict | None = None, max_bytes: int = 40_000_000) -> bytes:
        """Small binary download (thumbnails, preview frames), cached forever by URL."""
        path = self._cache_path("bin", url, None)
        if path.is_file():
            self.stats["cache_hits"] += 1
            return path.read_bytes()
        r = await self._request(url, None, headers)
        data = r.content
        if len(data) > max_bytes:
            raise HttpError(f"too large ({len(data)} bytes): {url}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    async def download(self, url: str, dest: Path, headers: dict | None = None,
                       max_bytes: int = 4_000_000_000) -> Path:
        """Stream a large file to disk (full-resolution fetch)."""
        if self.offline:
            raise HttpError(f"offline: {url}")
        host = urlsplit(url).hostname or ""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        sem = await self._slot(host)
        try:
            async with self._client.stream("GET", url, headers=headers) as r:
                if r.status_code >= 400:
                    raise HttpError(f"HTTP {r.status_code} for {url}")
                n = 0
                with open(tmp, "wb") as f:
                    async for chunk in r.aiter_bytes(1 << 20):
                        n += len(chunk)
                        if n > max_bytes:
                            raise HttpError(f"larger than {max_bytes} bytes: {url}")
                        f.write(chunk)
                self.last_content_type = r.headers.get("content-type", "")
        finally:
            sem.release()
        tmp.replace(dest)
        return dest

