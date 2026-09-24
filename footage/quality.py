"""Image checks on thumbnails: perceptual hash, sharpness, overlaid text/watermark, monochrome.

All measured on the thumbnail resized to 512 px on the long edge, so scores compare across
sources. OpenCV is used when installed; without it the text heuristic is skipped.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageOps

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import imagehash  # type: ignore
except ImportError:  # pragma: no cover
    imagehash = None

NORM = 512
BLUR_REJECT = 25.0        # Laplacian variance below this at 512 px ≈ unusably soft
BLUR_GOOD = 300.0


def load(data: bytes | str) -> Image.Image:
    im = Image.open(io.BytesIO(data) if isinstance(data, (bytes, bytearray)) else data)
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (128, 128, 128))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    return im.convert("RGB")


def _norm(im: Image.Image) -> Image.Image:
    im = im.copy()
    im.thumbnail((NORM, NORM), Image.LANCZOS)
    return im


def phash(im: Image.Image) -> str:
    if imagehash is not None:
        return str(imagehash.phash(im, hash_size=8))
    g = np.asarray(im.convert("L").resize((9, 8), Image.LANCZOS), dtype=np.int16)   # dHash fallback
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def sharpness(im: Image.Image) -> float:
    g = np.asarray(_norm(im).convert("L"), dtype=np.float32)
    if cv2 is not None:
        return float(cv2.Laplacian(g, cv2.CV_32F).var())
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def text_regions(im: Image.Image) -> dict:
    """Heuristic detector for overlaid text, captions, logos and watermarks.

    Text strokes give strong, dense morphological gradients in horizontal runs. We close them into
    line blobs and keep blobs shaped like text lines. Returns the covered area fraction and whether
    any sits in a corner / bottom band (typical watermark and news-ticker positions).
    """
    if cv2 is None:
        return {"text_area": None, "text_boxes": 0, "corner_text": None}
    g = np.asarray(_norm(im).convert("L"))
    h, w = g.shape
    grad = cv2.morphologyEx(g, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 1)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    area, boxes, corner = 0, 0, False
    for i in range(1, n):
        x, y, bw_, bh, a = stats[i]
        if not (6 <= bh <= 0.12 * h and bw_ >= 2.5 * bh and bw_ <= 0.95 * w):
            continue
        fill = a / float(bw_ * bh)
        if fill < 0.45:
            continue
        boxes += 1
        area += bw_ * bh
        cx, cy = x + bw_ / 2, y + bh / 2
        if (cy > 0.82 * h or cy < 0.12 * h) and (cx < 0.3 * w or cx > 0.7 * w or cy > 0.82 * h):
            corner = True
    return {"text_area": round(area / float(w * h), 4), "text_boxes": boxes, "corner_text": corner}


def colorfulness(im: Image.Image) -> float:
    a = np.asarray(_norm(im), dtype=np.float32)
    rg = a[..., 0] - a[..., 1]
    yb = 0.5 * (a[..., 0] + a[..., 1]) - a[..., 2]
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def analyse(im: Image.Image) -> dict:
    t = text_regions(im)
    col = colorfulness(im)
    return {"phash": phash(im), "sharpness": round(sharpness(im), 1), **t,
            "colorfulness": round(col, 1), "mono": col < 8.0, "thumb_size": list(im.size)}


def quality_score(q: dict) -> float:
    """0..1 from sharpness and absence of overlaid text."""
    s = q.get("sharpness") or 0.0
    sharp = min(1.0, max(0.0, (np.log10(max(s, 1)) - np.log10(BLUR_REJECT)) /
                         (np.log10(BLUR_GOOD) - np.log10(BLUR_REJECT))))
    ta = q.get("text_area") or 0.0
    text_pen = min(1.0, ta / 0.08) * 0.6 + (0.3 if q.get("corner_text") else 0.0)
    return round(max(0.0, sharp * (1 - text_pen)), 3)
