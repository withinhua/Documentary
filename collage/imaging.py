"""Procedural paper-collage imaging: halftone, paper, torn cards, strips, tape, sticker cut-outs.

Every function returns either an RGB uint8 image or a `Sprite` (premultiplied float32 RGBA).
All randomness is seeded so renders are deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .assets import FONT

W, H = 1920, 1080

PAPER = (224, 211, 184)       # warm beige background paper
STRIP = (241, 235, 222)       # off-white typewriter strip
INK = (30, 26, 23)            # typewriter / halftone ink
NUMERAL = (74, 35, 24)        # dark brown numerals on the accent card
STICKER = (246, 243, 236)     # white sticker border


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------------------- sprites
@dataclass
class Sprite:
    """Premultiplied RGBA float32 image (h, w, 4) with values 0..1."""
    px: np.ndarray

    @property
    def w(self):
        return self.px.shape[1]

    @property
    def h(self):
        return self.px.shape[0]

    @staticmethod
    def from_rgba(rgb: np.ndarray, alpha: np.ndarray) -> "Sprite":
        a = alpha.astype(np.float32)
        if a.max() > 1.0:
            a = a / 255.0
        c = rgb.astype(np.float32) / 255.0
        return Sprite(np.dstack([c * a[..., None], a]).astype(np.float32))


def over(dst: Sprite, src: Sprite, x: int, y: int) -> Sprite:
    """Paste src over dst at integer offset (in place, clipped). Returns dst."""
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dst.w, x + src.w), min(dst.h, y + src.h)
    if x1 <= x0 or y1 <= y0:
        return dst
    s = src.px[y0 - y:y1 - y, x0 - x:x1 - x]
    d = dst.px[y0:y1, x0:x1]
    d *= (1.0 - s[..., 3:4])
    d += s
    return dst


def with_shadow(sp: Sprite, offset=(8, 12), blur=14, opacity=0.45, pad=None) -> Sprite:
    """Return a new sprite = soft drop shadow + sprite (padded so the shadow fits)."""
    pad = pad if pad is not None else blur * 2 + max(abs(offset[0]), abs(offset[1]))
    h, w = sp.h + 2 * pad, sp.w + 2 * pad
    a = np.zeros((h, w), np.float32)
    a[pad + offset[1]:pad + offset[1] + sp.h, pad + offset[0]:pad + offset[0] + sp.w] = sp.px[..., 3]
    a = cv2.GaussianBlur(a, (0, 0), blur) * opacity
    out = Sprite(np.zeros((h, w, 4), np.float32))
    out.px[..., 3] = a
    out.px[..., :3] = a[..., None] * np.array([20, 14, 8], np.float32)[None, None] / 255.0
    return over(out, sp, pad, pad)


# --------------------------------------------------------------------------------------- noise
def fbm(h: int, w: int, seed: int, scales=(64, 16, 4), weights=None) -> np.ndarray:
    """Cheap fractal value-noise in 0..1 (sum of upscaled random grids)."""
    rng = np.random.default_rng(seed)
    weights = weights or [1.0 / (i + 1) for i in range(len(scales))]
    acc = np.zeros((h, w), np.float32)
    for s, wt in zip(scales, weights):
        gh, gw = max(2, h // s + 2), max(2, w // s + 2)
        g = rng.random((gh, gw), dtype=np.float32)
        acc += wt * cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC)
    acc -= acc.min()
    acc /= max(1e-6, acc.max())
    return acc


def noise1d(n: int, seed: int, scales=(40, 9, 3), amps=(1.0, 0.55, 0.35)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.zeros(n, np.float32)
    xs = np.arange(n, dtype=np.float32)
    for s, a in zip(scales, amps):
        k = n // s + 3
        pts = rng.uniform(-1, 1, k).astype(np.float32)
        out += a * np.interp(xs / s, np.arange(k), pts)
    return out


# --------------------------------------------------------------------------------------- paper
@lru_cache(maxsize=8)
def _paper_cached(w, h, color, seed):
    base = np.array(color, np.float32)[None, None]
    low = fbm(h, w, seed, (320, 90), (1.0, 0.5)) - 0.5
    mid = fbm(h, w, seed + 1, (24, 6), (1.0, 0.6)) - 0.5
    rng = np.random.default_rng(seed + 2)
    fine = rng.normal(0, 1, (h, w)).astype(np.float32)
    fine = cv2.GaussianBlur(fine, (0, 0), 0.8)
    # paper fibres: short faint strokes
    fib = np.zeros((h, w), np.float32)
    for _ in range(int(w * h / 2500)):
        x, y = rng.integers(0, w), rng.integers(0, h)
        ang = rng.uniform(0, np.pi)
        ln = rng.uniform(4, 18)
        cv2.line(fib, (int(x), int(y)), (int(x + ln * np.cos(ang)), int(y + ln * np.sin(ang))),
                 float(rng.uniform(-1, 1)), 1, cv2.LINE_AA)
    lum = 1.0 + 0.07 * low + 0.035 * mid + 0.018 * fine + 0.03 * fib
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    vig = 1.0 - 0.10 * (((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2) * 2.2)
    img = base * (lum * vig)[..., None]
    # a faint warm/cool colour mottling
    img[..., 2] *= 1.0 - 0.03 * low
    return np.clip(img, 0, 255).astype(np.uint8)


def paper(w=W, h=H, color=PAPER, seed=7) -> np.ndarray:
    return _paper_cached(w, h, tuple(color), seed).copy()


# --------------------------------------------------------------------------------------- photo tone
def autocontrast(gray: np.ndarray, lo=1.0, hi=99.0) -> np.ndarray:
    a, b = np.percentile(gray, [lo, hi])
    return np.clip((gray - a) / max(1e-3, b - a), 0, 1)


def prep_gray(rgb: np.ndarray, contrast=1.25, mask=None) -> np.ndarray:
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    if mask is not None and mask.mean() > 0.02:
        sel = g[mask > 0.5]
        a, b = np.percentile(sel, [1, 99])
        g = np.clip((g - a) / max(1e-3, b - a), 0, 1)
    else:
        g = autocontrast(g)
    # unsharp for that crunchy newsprint detail
    blur = cv2.GaussianBlur(g, (0, 0), 2.0)
    g = np.clip(g + 0.6 * (g - blur), 0, 1)
    # S-curve contrast
    g = np.clip(0.5 + (g - 0.5) * contrast, 0, 1)
    return g


def halftone(gray: np.ndarray, cell=6.0, angle=45.0, ink=INK, paper_rgb=STICKER, mix=0.35,
             seed=3) -> np.ndarray:
    """Real dot-screen halftone. gray: float 0..1 (1 = white). Returns RGB uint8.

    Uses a Euclidean-dot screen function on a rotated grid so the dots grow with darkness and merge
    into a checkerboard in the midtones, like newspaper print. `mix` blends some continuous tone back
    in so faces stay readable at 1080p.
    """
    h, w = gray.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    th = np.deg2rad(angle)
    u = (xx * np.cos(th) + yy * np.sin(th)) * (2 * np.pi / cell)
    v = (-xx * np.sin(th) + yy * np.cos(th)) * (2 * np.pi / cell)
    screen = 0.5 + 0.25 * (np.cos(u) + np.cos(v))          # 0..1 threshold map
    dark = 1.0 - cv2.GaussianBlur(gray, (0, 0), cell * 0.18)
    # soft (anti-aliased) threshold
    ink_amt = np.clip((dark - screen) * 3.2 + 0.5, 0, 1)
    ink_amt = (1 - mix) * ink_amt + mix * (1.0 - gray)
    # slight print unevenness
    ink_amt *= 0.93 + 0.07 * fbm(h, w, seed, (40, 8))
    ink = np.array(ink, np.float32)[None, None]
    pap = np.array(paper_rgb, np.float32)[None, None]
    out = pap + (ink - pap) * ink_amt[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def archival(rgb: np.ndarray, seed=5, warm=True) -> np.ndarray:
    g = prep_gray(rgb, contrast=1.12)
    h, w = g.shape
    rng = np.random.default_rng(seed)
    g = g * 0.9 + 0.05
    g += cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 0.9) * 0.045
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    g *= 1.0 - 0.35 * (((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2) * 2.0)
    g = np.clip(g, 0, 1)
    tint = np.array([1.0, 0.95, 0.86] if warm else [1, 1, 1], np.float32)
    lo = np.array([22, 18, 15], np.float32)
    hi = np.array([238, 229, 208], np.float32) * tint / tint.max()
    out = lo + (hi - lo) * g[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def tone(rgb: np.ndarray, style: str, cell=6.0, seed=3, mask=None) -> np.ndarray:
    if style == "clean":
        return rgb
    if style == "archival":
        return archival(rgb, seed)
    return halftone(prep_gray(rgb, mask=mask), cell=cell, seed=seed)


def cover(rgb: np.ndarray, w: int, h: int, focus=(0.5, 0.5)) -> np.ndarray:
    """Resize+crop to fill w x h."""
    sh, sw = rgb.shape[:2]
    s = max(w / sw, h / sh)
    nw, nh = int(np.ceil(sw * s)), int(np.ceil(sh * s))
    r = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    x0 = int((nw - w) * focus[0])
    y0 = int((nh - h) * focus[1])
    return r[y0:y0 + h, x0:x0 + w]


# --------------------------------------------------------------------------------------- sticker cut-out
def sticker(rgb: np.ndarray, alpha: np.ndarray, target_h: int, border=16, style="halftone",
            cell=6.0, seed=11, shadow=True, max_w: int | None = None) -> Sprite:
    """Cut the subject out, halftone it, add a hand-cut white sticker border and drop shadow.

    The subject's bounding box is scaled so its height is `target_h` px.
    """
    a = alpha.astype(np.float32)
    a = np.clip((a - 0.2) / 0.6, 0, 1)
    ys, xs = np.where(a > 0.5)
    if len(xs) == 0:
        ys, xs = np.array([0, a.shape[0] - 1]), np.array([0, a.shape[1] - 1])
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    s = target_h / (y1 - y0)
    if max_w and (x1 - x0) * s > max_w:
        s = max_w / (x1 - x0)
    crop_rgb = rgb[y0:y1, x0:x1]
    crop_a = a[y0:y1, x0:x1]
    nw, nh = max(1, int(round((x1 - x0) * s))), max(1, int(round((y1 - y0) * s)))
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC
    crop_rgb = cv2.resize(crop_rgb, (nw, nh), interpolation=interp)
    crop_a = np.clip(cv2.resize(crop_a, (nw, nh), interpolation=cv2.INTER_LINEAR), 0, 1)

    pad = border * 3
    ph, pw = nh + 2 * pad, nw + 2 * pad
    A = np.zeros((ph, pw), np.float32)
    A[pad:pad + nh, pad:pad + nw] = crop_a
    C = np.zeros((ph, pw, 3), np.uint8)
    C[pad:pad + nh, pad:pad + nw] = crop_rgb

    if style == "clean":
        toned = C
    elif style == "archival":
        toned = archival(C, seed)
    else:
        toned = halftone(prep_gray(C, mask=A), cell=cell, seed=seed)

    # hand-cut outline: distance from subject, jittered, then smoothed so it reads as scissor cuts
    binm = (A > 0.5).astype(np.uint8)
    dist = cv2.distanceTransform(1 - binm, cv2.DIST_L2, 5)
    jitter = (fbm(ph, pw, seed + 1, (90, 30), (1.0, 0.4)) - 0.5) * border * 0.7
    outer = (dist < border + jitter).astype(np.float32)
    outer = cv2.GaussianBlur(outer, (0, 0), border * 0.45)
    outer = np.clip((outer - 0.5) * 6 + 0.5, 0, 1)
    outer = np.maximum(outer, A)
    # subject edges hard-ish so halftone meets the white border crisply
    inner = np.clip((A - 0.5) * 4 + 0.5, 0, 1)
    border_rgb = np.array(STICKER, np.float32)[None, None] * (0.97 + 0.03 * fbm(ph, pw, seed + 2, (12, 3)))[..., None]
    rgbf = border_rgb * (1 - inner[..., None]) + toned.astype(np.float32) * inner[..., None]
    # clip the sprite to the image bounds (photo crop edges stay straight, like a cut print)
    sp = Sprite.from_rgba(np.clip(rgbf, 0, 255), outer)
    return with_shadow(sp, offset=(6, 10), blur=12, opacity=0.5) if shadow else sp


# --------------------------------------------------------------------------------------- torn paper
def torn_mask(w: int, h: int, seed: int, rough=5.0, pad=12, step=3) -> np.ndarray:
    """Alpha (h+2pad, w+2pad) for a rectangle whose four edges are deckled/torn."""
    pts = []
    rng_seed = seed
    def side(n):
        nonlocal rng_seed
        rng_seed += 17
        return noise1d(n, rng_seed) * rough
    nx, ny = w // step + 1, h // step + 1
    top, bot, lef, rig = side(nx), side(nx), side(ny), side(ny)
    for i in range(nx):
        pts.append((pad + i * step, pad + top[i]))
    for j in range(ny):
        pts.append((pad + w + rig[j], pad + j * step))
    for i in range(nx - 1, -1, -1):
        pts.append((pad + i * step, pad + h + bot[i]))
    for j in range(ny - 1, -1, -1):
        pts.append((pad + lef[j], pad + j * step))
    ss = 4
    m = np.zeros(((h + 2 * pad) * ss, (w + 2 * pad) * ss), np.uint8)
    cv2.fillPoly(m, [np.round(np.array(pts) * ss).astype(np.int32)], 255, cv2.LINE_AA)
    m = cv2.resize(m, (w + 2 * pad, h + 2 * pad), interpolation=cv2.INTER_AREA)
    return m.astype(np.float32) / 255.0


def card(w: int, h: int, color, seed=21, rough=6.0) -> Sprite:
    """Accent card with torn edges and a mottled, grainy print texture."""
    a = torn_mask(w, h, seed, rough=rough)
    hh, ww = a.shape
    col = np.array(color, np.float32)[None, None]
    mott = fbm(hh, ww, seed + 3, (120, 30, 6), (1.0, 0.6, 0.5)) - 0.5
    rng = np.random.default_rng(seed)
    grain = cv2.GaussianBlur(rng.normal(0, 1, (hh, ww)).astype(np.float32), (0, 0), 0.7)
    lum = 1.0 + 0.07 * mott + 0.035 * grain
    rgb = col * lum[..., None]
    # torn edges show a slightly lighter fibrous rim
    edge = cv2.GaussianBlur(a, (0, 0), 1.2)
    rim = np.clip((1 - edge) * 2.2, 0, 1) * a
    rgb = rgb * (1 - 0.25 * rim[..., None]) + 255 * 0.25 * rim[..., None]
    return Sprite.from_rgba(np.clip(rgb, 0, 255), a)


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONT[kind]), size)
    if kind == "display_serif":
        try:
            f.set_variation_by_axes([800])
        except Exception:
            pass
    return f


def text_mask(text: str, kind: str, size: int, tracking=0, line_gap=1.12, align="left") -> np.ndarray:
    """Render (possibly multi-line) text to an alpha mask float32, tightly cropped."""
    f = font(kind, size)
    lines = text.split("\n")
    asc, desc = f.getmetrics()
    lh = int((asc + desc) * line_gap)
    widths = []
    for ln in lines:
        if tracking:
            widths.append(int(sum(f.getlength(c) + tracking for c in ln) - tracking) if ln else 0)
        else:
            widths.append(int(f.getlength(ln)))
    W_ = max(widths) + size
    H_ = lh * len(lines) + size
    im = Image.new("L", (W_, H_), 0)
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        x = size // 2
        if align == "center":
            x += (max(widths) - widths[i]) // 2
        y = size // 2 + i * lh
        if tracking:
            for c in ln:
                d.text((x, y), c, font=f, fill=255)
                x += f.getlength(c) + tracking
        else:
            d.text((x, y), ln, font=f, fill=255)
    m = np.asarray(im).astype(np.float32) / 255.0
    ys, xs = np.where(m > 0.02)
    if len(xs) == 0:
        return np.zeros((1, 1), np.float32)
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def wrap(text: str, kind: str, size: int, max_w: int) -> str:
    f = font(kind, size)
    out, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if f.getlength(t) <= max_w or not cur:
            cur = t
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    return "\n".join(out)


def grainy(mask: np.ndarray, seed=5, holes=0.28, rough=0.6) -> np.ndarray:
    """Distress a text mask: speckled holes + eroded, uneven edges (worn rubber-stamp print)."""
    h, w = mask.shape
    rng = np.random.default_rng(seed)
    n = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 0.9)
    n = (n - n.mean()) / (n.std() + 1e-6)
    thr = np.quantile(n, holes)
    speck = np.clip((n - thr) * 2.5 + 0.5, 0, 1)
    edge_n = fbm(h, w, seed + 1, (10, 3), (1.0, 0.7))
    m = np.clip((mask - 0.5 + (edge_n - 0.5) * rough) * 3 + 0.5, 0, 1) * (mask > 0.02)
    return m * (0.35 + 0.65 * speck)


def ink_sprite(mask: np.ndarray, color, pad=0) -> Sprite:
    if pad:
        mask = np.pad(mask, pad)
    rgb = np.broadcast_to(np.array(color, np.float32)[None, None], mask.shape + (3,))
    return Sprite.from_rgba(rgb, mask)


def strip(text: str, size=64, kind="typewriter", pad=(34, 18), color=STRIP, ink=INK, seed=9,
          min_w=0, shadow=True, align="left") -> Sprite:
    """Off-white paper strip with typewriter text (slightly uneven ink)."""
    m = text_mask(text, kind, size, align=align)
    th, tw = m.shape
    w = max(min_w, tw + 2 * pad[0])
    h = th + 2 * pad[1]
    a = torn_mask(w, h, seed, rough=1.6, pad=6, step=6)
    hh, ww = a.shape
    col = np.array(color, np.float32)[None, None]
    lum = 1.0 + 0.04 * (fbm(hh, ww, seed + 2, (80, 12, 3), (1.0, 0.5, 0.4)) - 0.5)
    rgb = col * lum[..., None]
    # ink with typewriter unevenness
    inkv = 0.78 + 0.22 * fbm(th, tw, seed + 4, (6, 2), (1.0, 0.8))
    tm = np.clip(m * 1.15, 0, 1) * inkv
    ox = 6 + (w - tw) // 2 if align == "center" or min_w else 6 + pad[0]
    oy = 6 + pad[1]
    reg = rgb[oy:oy + th, ox:ox + tw]
    reg[:] = reg * (1 - tm[..., None]) + np.array(ink, np.float32)[None, None] * tm[..., None]
    sp = Sprite.from_rgba(np.clip(rgb, 0, 255), a)
    return with_shadow(sp, offset=(3, 5), blur=6, opacity=0.35) if shadow else sp


def tape(w=190, h=58, seed=13) -> Sprite:
    """Translucent masking-tape piece with zig-zag torn ends."""
    pad = 8
    ss = 4
    rng = np.random.default_rng(seed)
    pts = [(pad, pad)]
    # top edge straight, right end jagged, bottom straight, left end jagged
    pts += [(pad + w, pad)]
    for j in range(0, h + 1, 5):
        pts.append((pad + w + rng.uniform(-6, 6), pad + j))
    pts += [(pad + w, pad + h), (pad, pad + h)]
    for j in range(h, -1, -5):
        pts.append((pad + rng.uniform(-6, 6), pad + j))
    m = np.zeros(((h + 2 * pad) * ss, (w + 2 * pad) * ss), np.uint8)
    cv2.fillPoly(m, [np.round(np.array(pts) * ss).astype(np.int32)], 255, cv2.LINE_AA)
    a = cv2.resize(m, (w + 2 * pad, h + 2 * pad), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    hh, ww = a.shape
    tex = fbm(hh, ww, seed + 1, (30, 6, 2), (1.0, 0.5, 0.5))
    rgb = np.array([226, 216, 190], np.float32)[None, None] * (0.95 + 0.08 * tex)[..., None]
    return Sprite.from_rgba(rgb, a * (0.62 + 0.12 * tex))


def solid(w: int, h: int, rgb) -> Sprite:
    return Sprite.from_rgba(np.broadcast_to(np.array(rgb, np.uint8)[None, None], (h, w, 3)), np.ones((h, w), np.float32))


def rgb_sprite(img: np.ndarray) -> Sprite:
    return Sprite.from_rgba(img, np.ones(img.shape[:2], np.float32))
