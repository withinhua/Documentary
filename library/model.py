"""The library's vision-language model (open_clip), loaded once per process.

Default: SigLIP 2 ViT-B/32 at 256 px (webli). On this 4-core shared box it embeds ~7 img/s on
2 threads under load (SigLIP ViT-B/16: ~4 img/s; CLIP ViT-B/32: ~14 img/s but weaker on logos,
text and products). Override with LIBRARY_MODEL=<arch>/<pretrained>, e.g.
    LIBRARY_MODEL=ViT-B-16-SigLIP/webli   LIBRARY_MODEL=ViT-B-32/laion2b_s34b_b79k
Threads: LIBRARY_THREADS (default 2, the box is shared).
"""
from __future__ import annotations

import os
import threading

import numpy as np

DEFAULT = "ViT-B-32-SigLIP2-256/webli"
_LOCK = threading.Lock()
_CACHE: dict[str, "Model"] = {}


def model_name() -> str:
    return os.environ.get("LIBRARY_MODEL", DEFAULT)


class Model:
    def __init__(self, name: str | None = None):
        import open_clip
        import torch
        self.name = name or model_name()
        arch, _, pre = self.name.partition("/")
        torch.set_num_threads(int(os.environ.get("LIBRARY_THREADS", "2")))
        self.torch = torch
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(arch, pretrained=pre or None)
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(arch)
        self.siglip = "SigLIP" in arch
        m = self.model
        self.logit_scale = float(m.logit_scale.exp()) if hasattr(m, "logit_scale") else 100.0
        bias = getattr(m, "logit_bias", None)
        self.logit_bias = float(bias) if bias is not None else 0.0

    def images(self, ims: list, batch: int = 32) -> np.ndarray:
        out = []
        with self.torch.no_grad():
            for i in range(0, len(ims), batch):
                x = self.torch.stack([self.preprocess(im) for im in ims[i:i + batch]])
                f = self.model.encode_image(x)
                out.append((f / f.norm(dim=-1, keepdim=True)).numpy())
        return np.concatenate(out).astype(np.float32) if out else np.zeros((0, 1), np.float32)

    def texts(self, texts: list[str], batch: int = 64) -> np.ndarray:
        out = []
        with self.torch.no_grad():
            for i in range(0, len(texts), batch):
                f = self.model.encode_text(self.tokenizer(texts[i:i + batch]))
                out.append((f / f.norm(dim=-1, keepdim=True)).numpy())
        return np.concatenate(out).astype(np.float32) if out else np.zeros((0, 1), np.float32)

    def prob(self, cos: np.ndarray) -> np.ndarray:
        """SigLIP's own pairwise probability that an image matches a caption."""
        return 1.0 / (1.0 + np.exp(-(self.logit_scale * cos + self.logit_bias)))


def get(name: str | None = None) -> Model:
    name = name or model_name()
    with _LOCK:
        if name not in _CACHE:
            _CACHE[name] = Model(name)
        return _CACHE[name]


def cached_texts(con, texts: list[str], name: str | None = None) -> np.ndarray:
    """Text embeddings through the SQLite prompt cache: repeated prompts cost no model time
    (and a fully cached batch never loads the model)."""
    name = name or model_name()
    got: dict[str, np.ndarray] = {}
    uniq = list(dict.fromkeys(texts))
    for i in range(0, len(uniq), 500):
        chunk = uniq[i:i + 500]
        q = f"SELECT text, vec FROM prompt_cache WHERE model=? AND text IN ({','.join('?' * len(chunk))})"
        for t, v in con.execute(q, [name, *chunk]):
            got[t] = np.frombuffer(v, dtype=np.float16).astype(np.float32)
    miss = [t for t in uniq if t not in got]
    if miss:
        vecs = get(name).texts(miss)
        for t, v in zip(miss, vecs):
            got[t] = v
            con.execute("INSERT OR REPLACE INTO prompt_cache VALUES (?,?,?)",
                        (name, t, v.astype(np.float16).tobytes()))
        con.commit()
    m = np.stack([got[t] for t in texts])
    return m / np.linalg.norm(m, axis=1, keepdims=True)
