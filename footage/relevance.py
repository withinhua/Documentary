"""Optional local "eyes": CLIP / SigLIP relevance of each thumbnail to subject + must_show.

Uses open_clip when installed and its weights can be loaded (Hugging Face download or a local
file via FOOTAGE_CLIP_PRETRAINED=/path/to/weights.bin). Anything missing → returns None and the
pipeline ranks on metadata + quality; Claude's review is the final judge either way.

    pip install open_clip_torch torch --index-url https://download.pytorch.org/whl/cpu   # optional
    FOOTAGE_CLIP_MODEL=ViT-B-32  FOOTAGE_CLIP_PRETRAINED=laion2b_s34b_b79k   (defaults)
    FOOTAGE_CLIP_MODEL=ViT-B-16-SigLIP  FOOTAGE_CLIP_PRETRAINED=webli         (SigLIP alternative)
"""
from __future__ import annotations

import os
import sys

_MODEL = None
_FAILED: str | None = None


def load():
    global _MODEL, _FAILED
    if _MODEL is not None or _FAILED:
        return _MODEL
    try:
        import open_clip  # type: ignore
        import torch  # type: ignore
        name = os.environ.get("FOOTAGE_CLIP_MODEL", "ViT-B-32")
        pre = os.environ.get("FOOTAGE_CLIP_PRETRAINED", "laion2b_s34b_b79k")
        torch.set_num_threads(max(1, os.cpu_count() or 1))
        model, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=pre)
        model.eval()
        tok = open_clip.get_tokenizer(name)
        _MODEL = (model, preprocess, tok, torch)
    except Exception as e:  # ImportError, download blocked, bad weights …
        _FAILED = f"{type(e).__name__}: {str(e)[:160]}"
        print(f"[footage] CLIP relevance unavailable ({_FAILED}); ranking without it", file=sys.stderr)
    return _MODEL


def status() -> str:
    return "on" if _MODEL is not None else (f"off ({_FAILED})" if _FAILED else "not loaded")


def prompts(subject: str, must_show: str, era: str, kind: str) -> list[str]:
    what = "a still from a film of" if kind == "video" else "a photograph of"
    ps = [f"{what} {subject}"]
    if must_show:
        ps.append(f"{what} {subject}, {must_show}")
    if era:
        ps.append(f"{subject}, {era}")
    return ps


def score(images: list, texts: list[str], batch: int = 32) -> list[float] | None:
    """Max cosine similarity of each PIL image to any of the prompts, mapped to ~0..1."""
    m = load()
    if m is None or not images:
        return None
    model, preprocess, tok, torch = m
    with torch.no_grad():
        t = model.encode_text(tok(texts))
        t = t / t.norm(dim=-1, keepdim=True)
        sims = []
        for i in range(0, len(images), batch):
            x = torch.stack([preprocess(im) for im in images[i:i + batch]])
            f = model.encode_image(x)
            f = f / f.norm(dim=-1, keepdim=True)
            sims.extend((f @ t.T).max(dim=1).values.tolist())
    # CLIP cosine for a true match is ~0.25-0.35, unrelated ~0.10-0.18 → stretch to 0..1
    return [round(min(1.0, max(0.0, (s - 0.12) / 0.22)), 3) for s in sims]
