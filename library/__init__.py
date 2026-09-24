"""Pre-built, indexed media library: harvest once per topic, then rank every beat in milliseconds.

    python -m library.ingest --topic "Coca-Cola" --era 1940-1990 --from-candidates 'projects/*/footage/candidates.json'
    python -m library.embed                                   # embed new thumbnails/keyframes + metadata text
    python -m library.search "New Coke can, 1985" --era 1983-1986
    python -m library.pick projects/new-coke/requests.json    # top-k for every beat + chapter review boards

Stages: ingest (footage/sources adapters → SQLite + 512 px thumbs + phash dedupe) → embed
(open_clip SigLIP, image + text vectors) → search (vector + keyword + era/licence/resolution
filters) → judge (local zero-shot probes, optional Jev) → boards (one sheet per chapter) for
Claude's vision review → picks.template.json + library_candidates.json for footage.fetch.
"""


def __getattr__(name):              # lazy: `python -m library.ingest` must not import torch
    if name in ("Library", "search"):
        from . import search as _s
        return getattr(_s, name)
    raise AttributeError(name)
