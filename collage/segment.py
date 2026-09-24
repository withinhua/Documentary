"""Subject cut-out with the ISNet (rembg "isnet-general-use") ONNX model, CPU only.

Masks are cached next to the render workdir keyed by the file hash, so re-rendering a
scene (or using the same photo in several scenes) runs the model once.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import cv2
import numpy as np

from .assets import model_path

_SESS = {}
_LOCK = threading.Lock()


def _session(name: str):
    import onnxruntime as ort

    with _LOCK:
        if name not in _SESS:
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            _SESS[name] = ort.InferenceSession(str(model_path(name)), opts, providers=["CPUExecutionProvider"])
        return _SESS[name]


def _raw_mask(rgb: np.ndarray, model: str) -> np.ndarray:
    size = 1024 if model.startswith("isnet") else 320
    x = cv2.resize(rgb, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    if model.startswith("isnet"):
        x = (x - 0.5) / 1.0
    else:
        x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    s = _session(model)
    out = s.run(None, {s.get_inputs()[0].name: x})[0][0, 0]
    out = (out - out.min()) / max(1e-6, out.max() - out.min())
    return cv2.resize(out, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_LINEAR)


def _select(mask: np.ndarray, subject) -> np.ndarray:
    """Keep the chosen connected component(s). subject: 'all' | 'largest' | [x, y] (0..1)."""
    if subject == "all":
        return mask
    binm = (mask > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(binm, 8)
    if n <= 2:
        return mask
    if isinstance(subject, (list, tuple)):
        px = int(np.clip(subject[0], 0, 1) * (mask.shape[1] - 1))
        py = int(np.clip(subject[1], 0, 1) * (mask.shape[0] - 1))
        keep = lab[py, px]
        if keep == 0:
            keep = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        keep_ids = [keep]
    else:
        areas = stats[1:, cv2.CC_STAT_AREA]
        big = areas.max()
        # the largest blob plus anything else that is substantial (e.g. a person split in two)
        keep_ids = [i + 1 for i, a in enumerate(areas) if a >= 0.25 * big]
    sel = np.isin(lab, keep_ids).astype(np.uint8)
    sel = cv2.dilate(sel, np.ones((9, 9), np.uint8))  # keep soft edges around the kept blobs
    return mask * sel


def cutout_mask(photo: str | Path, cache_dir: Path | None = None, subject="largest",
                model: str = "isnet-general-use.onnx") -> np.ndarray:
    """Return float32 alpha (H, W) in 0..1 for the photo's main subject."""
    photo = Path(photo)
    rgb = load_rgb(photo)
    key = hashlib.sha1(photo.read_bytes()).hexdigest()[:16] + "_" + model.split(".")[0]
    cached = cache_dir / f"mask_{key}.png" if cache_dir else None
    if cached and cached.exists():
        m = cv2.imread(str(cached), cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
    else:
        try:
            m = _raw_mask(rgb, model)
        except Exception:
            if model == "u2netp.onnx":
                raise
            m = _raw_mask(rgb, "u2netp.onnx")
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(cached), (m * 255).astype(np.uint8))
    return _select(m, subject)


def load_rgb(path: str | Path) -> np.ndarray:
    from PIL import Image, ImageOps

    im = Image.open(path)
    im = ImageOps.exif_transpose(im).convert("RGB")
    return np.asarray(im)
