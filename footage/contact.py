"""Contact sheets built for a vision model to judge: big numbered tiles, facts under each tile.

Sheet width is 1568 px (the long edge Claude sees without downscaling). 4 columns; a video tile
is a 2×2 filmstrip with a timestamp on every frame so a pick can name the seconds it wants.
Per-candidate zoom images (`contact/<id>/<n>.jpg`) show one candidate large for close calls.
"""
from __future__ import annotations

import math
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

W, COLS, M, G = 1568, 4, 16, 12
TILE_W = (W - 2 * M - (COLS - 1) * G) // COLS
IMG_H = 250
CAP_H = 58
HEADER_H = 112
BG, INK, MUTED, PANEL = (250, 249, 246), (20, 20, 20), (90, 90, 90), (28, 28, 28)
LIC_COLOR = {"public-domain": (22, 120, 50), "cc0": (22, 120, 50), "cc-by": (25, 90, 170),
             "cc-by-sa": (25, 90, 170), "other": (170, 110, 0), "unknown": (200, 30, 30)}
BADGE = (255, 212, 0)

_FONT_CACHE: dict = {}
_FONT_FILES = {
    False: ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "DejaVuSans.ttf", "Arial.ttf"],
    True: ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
           "DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
}


def font(size: int, bold: bool = False):
    key = (size, bold)
    if key not in _FONT_CACHE:
        f = None
        for path in _FONT_FILES[bold]:
            try:
                f = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
        _FONT_CACHE[key] = f or ImageFont.load_default(size=size)
    return _FONT_CACHE[key]


def fit_text(draw: ImageDraw.ImageDraw, text: str, f, max_w: int) -> str:
    if draw.textlength(text, font=f) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def fmt_time(t: float | None) -> str:
    if t is None:
        return "?"
    t = int(round(t))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def _open(path: str | None) -> Image.Image | None:
    if not path:
        return None
    try:
        return ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except Exception:
        return None


def _contain(im: Image.Image, w: int, h: int, bg=PANEL) -> Image.Image:
    canvas = Image.new("RGB", (w, h), bg)
    im = im.copy()
    s = min(w / im.width, h / im.height)                           # fit the box (up or down)
    im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    canvas.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
    return canvas


def _label(draw, xy, text, f, fill=(255, 255, 255), bg=(0, 0, 0), pad=4, anchor_right=False, anchor_bottom=False):
    x, y = xy
    tw = int(draw.textlength(text, font=f))
    th = f.size + 2
    if anchor_right:
        x -= tw + 2 * pad
    if anchor_bottom:
        y -= th + 2 * pad
    draw.rectangle([x, y, x + tw + 2 * pad, y + th + 2 * pad], fill=bg)
    draw.text((x + pad, y + pad - 1), text, font=f, fill=fill)
    return x, y, x + tw + 2 * pad, y + th + 2 * pad


def media_panel(entry: dict, w: int, h: int, stamp_size: int = 16) -> Image.Image:
    """Photo: the thumbnail. Video: 2×2 filmstrip with timestamps."""
    frames = [f for f in entry.get("frames") or [] if f.get("path")]
    if entry.get("kind") == "video" and len(frames) >= 2:
        panel = Image.new("RGB", (w, h), PANEL)
        fw, fh = (w - 2) // 2, (h - 2) // 2
        d = ImageDraw.Draw(panel)
        for i, fr in enumerate(frames[:4]):
            im = _open(fr["path"])
            if im is None:
                continue
            x, y = (i % 2) * (fw + 2), (i // 2) * (fh + 2)
            panel.paste(_contain(im, fw, fh), (x, y))
            _label(d, (x + 3, y + fh - 3), f"{fmt_time(fr.get('t'))}", font(stamp_size, True), anchor_bottom=True)
        return panel
    im = _open(entry.get("thumb_path")) or (_open(frames[0]["path"]) if frames else None)
    if im is None:
        panel = Image.new("RGB", (w, h), PANEL)
        ImageDraw.Draw(panel).text((w // 2, h // 2), "no preview", font=font(22), fill=(200, 200, 200), anchor="mm")
        return panel
    return _contain(im, w, h)


def warnings(entry: dict) -> list[str]:
    q = entry.get("quality") or {}
    out = []
    if entry.get("license") == "unknown":
        out.append("LICENCE?")
    if "fair-use" in (entry.get("license_flags") or []):
        out.append("FAIR-USE")
    if q.get("corner_text") or (q.get("text_area") or 0) > 0.04:
        out.append("TEXT")
    if (q.get("sharpness") or 999) < 60:
        out.append("SOFT")
    if entry.get("low_res"):
        out.append("LOW-RES")
    return out


def caption_lines(entry: dict) -> tuple[str, str]:
    res = f"{entry['width']}×{entry['height']}" if entry.get("width") and entry.get("height") else "size ?"
    lic = entry.get("license_name") or entry.get("license") or "?"
    line1 = f"{entry.get('source', '?')} · {lic} · {res}"
    title = entry.get("title") or ""
    date = entry.get("date") or ""
    line2 = f"{date[:10]} · {title}" if date else title
    return line1, line2


def tile(entry: dict) -> Image.Image:
    t = Image.new("RGB", (TILE_W, IMG_H + CAP_H), BG)
    t.paste(media_panel(entry, TILE_W, IMG_H), (0, 0))
    d = ImageDraw.Draw(t)
    # number badge
    num = str(entry["n"])
    f = font(44, True)
    tw = int(d.textlength(num, font=f))
    d.rectangle([0, 0, tw + 22, 56], fill=BADGE)
    d.text((11, 3), num, font=f, fill=(0, 0, 0))
    if entry.get("kind") == "video":
        dur = f" {fmt_time(entry['duration'])}" if entry.get("duration") else ""
        _label(d, (TILE_W - 4, 4), f"VIDEO{dur}", font(20, True), bg=(200, 20, 20), anchor_right=True)
    warn = warnings(entry)
    if warn:
        _label(d, (TILE_W - 4, IMG_H - 4), " ".join(warn), font(17, True), bg=(200, 20, 20),
               anchor_right=True, anchor_bottom=True)
    lic_col = LIC_COLOR.get(entry.get("license", "unknown"), INK)
    d.rectangle([0, IMG_H, 7, IMG_H + CAP_H - 4], fill=lic_col)       # licence colour bar
    l1, l2 = caption_lines(entry)
    d.text((13, IMG_H + 4), fit_text(d, l1, font(19, True), TILE_W - 16), font=font(19, True), fill=lic_col)
    d.text((13, IMG_H + 30), fit_text(d, l2, font(18), TILE_W - 16), font=font(18), fill=INK)
    return t


def header(req: dict, n: int, page: tuple[int, int] | None = None) -> Image.Image:
    h = Image.new("RGB", (W, HEADER_H), (18, 18, 18))
    d = ImageDraw.Draw(h)
    kind = (req.get("kind") or "photo").upper()
    tag = f"{req['id']} · {kind}"
    _label(d, (M, 14), tag, font(26, True), fill=(0, 0, 0), bg=BADGE, pad=6)
    x0 = M + int(d.textlength(tag, font=font(26, True))) + 30
    d.text((x0, 12), fit_text(d, req.get("subject", ""), font(36, True), W - x0 - M), font=font(36, True),
           fill=(255, 255, 255))
    bits = []
    if req.get("must_show"):
        bits.append(f"must show: {req['must_show']}")
    if req.get("era"):
        bits.append(f"era: {req['era']}")
    bits.append(f"min width {req.get('min_width', '?')}px")
    bits.append(f"{n} candidates" + (f" · sheet {page[0]}/{page[1]}" if page and page[1] > 1 else ""))
    d.text((M, 66), fit_text(d, "   |   ".join(bits), font(24), W - 2 * M), font=font(24), fill=(225, 225, 225))
    return h


def contact_sheet(req: dict, entries: list[dict], dest: Path, page: tuple[int, int] | None = None) -> Path:
    rows = max(1, math.ceil(len(entries) / COLS))
    H = HEADER_H + M + rows * (IMG_H + CAP_H + G) - G + M
    sheet = Image.new("RGB", (W, H), BG)
    sheet.paste(header(req, len(entries), page), (0, 0))
    for i, e in enumerate(entries):
        r, c = divmod(i, COLS)
        x = M + c * (TILE_W + G)
        y = HEADER_H + M + r * (IMG_H + CAP_H + G)
        sheet.paste(tile(e), (x, y))
    if not entries:
        ImageDraw.Draw(sheet).text((W // 2, HEADER_H + 120), "NO CANDIDATES — re-query", font=font(40, True),
                                   fill=(200, 30, 30), anchor="mm")
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest, quality=88)
    return dest


def zoom(req: dict, entry: dict, dest: Path, w: int = 1280, h: int = 800) -> Path:
    """One candidate, large, with its full facts (for close calls between two tiles)."""
    body = media_panel(entry, w, h, stamp_size=24)
    info_h = 150
    im = Image.new("RGB", (w, h + info_h), BG)
    im.paste(body, (0, 0))
    d = ImageDraw.Draw(im)
    num = f"{entry['n']}"
    d.rectangle([0, 0, int(d.textlength(num, font=font(64, True))) + 30, 80], fill=BADGE)
    d.text((15, 4), num, font=font(64, True), fill=(0, 0, 0))
    l1, l2 = caption_lines(entry)
    lic_col = LIC_COLOR.get(entry.get("license", "unknown"), INK)
    d.text((16, h + 8), fit_text(d, f"{req['id']} · {l1}", font(26, True), w - 32), font=font(26, True), fill=lic_col)
    d.text((16, h + 44), fit_text(d, l2, font(22), w - 32), font=font(22), fill=INK)
    desc = (entry.get("description") or "").strip()
    if desc:
        wrapped = textwrap.wrap(desc, 120)[:2]
        d.text((16, h + 78), "\n".join(wrapped), font=font(19), fill=MUTED)
    warn = warnings(entry)
    if warn:
        _label(d, (w - 8, 8), " ".join(warn), font(24, True), bg=(200, 20, 20), anchor_right=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, quality=88)
    return dest
