"""Source adapters. Each returns normalised `Candidate`s for one query.

Default sources need no key. Pexels/Pixabay switch on when PEXELS_API_KEY / PIXABAY_API_KEY are
set. NARA uses NARA_API_KEY when set. YouTube is opt-in only (copyrighted: fair-use risk).
"""
from __future__ import annotations

import os

from ..core import Candidate, Request
from ..net import Http


class Source:
    name = ""
    kinds: tuple[str, ...] = ("photo",)
    hosts: tuple[str, ...] = ()          # hostnames the network policy must allow
    default = True
    env_key: str | None = None           # required env var (None = keyless)

    def enabled(self) -> bool:
        return self.env_key is None or bool(os.environ.get(self.env_key))

    def wants(self, kind: str) -> bool:
        return kind == "any" or kind in self.kinds

    async def search(self, http: Http, req: Request, query: str, kind: str, limit: int) -> list[Candidate]:
        raise NotImplementedError


def registry() -> dict[str, Source]:
    from .archive import Archive
    from .commons import Commons
    from .loc import LOC
    from .nara import NARA
    from .openverse import Openverse
    from .smithsonian import Smithsonian
    from .stock import Pexels, Pixabay
    from .youtube import YouTube
    srcs = [Commons(), Openverse(), Archive(), LOC(), NARA(), Smithsonian(), Pexels(), Pixabay(), YouTube()]
    return {s.name: s for s in srcs}


def all_hosts() -> list[str]:
    return sorted({h for s in registry().values() for h in s.hosts})
