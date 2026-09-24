"""Scene builders: turn a spec dict into an `anim.Scene` (layers + camera)."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

import cv2
import numpy as np

from . import imaging as im
from .anim import (FPS, Camera, Layer, Scene, State, clamp01, drift, ease_in_out, ease_out_cubic, fade_in,
                   pop_in, slide_in, stepped)
from .imaging import H, W
from .segment import cutout_mask, load_rgb

DEFAULT_ACCENT = "#e8603c"


def _seed(spec) -> int:
    return int(hashlib.md5(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:6], 16)


# --------------------------------------------------------------------------------------- camera
def camera_for(motion: str, dur: float, strength: float = 1.0) -> Camera:
    e = lambda t: ease_in_out(t / dur) * 0.6 + (t / dur) * 0.4  # mostly linear, soft ends
    s = strength
    if motion == "pull":
        return Camera(zoom=lambda t: 1.10 * s + (1 - s) - 0.09 * s * e(t))
    if motion in ("left", "right"):
        d = -1 if motion == "left" else 1
        return Camera(zoom=lambda t: 1.0 + 0.12 * s + 0.02 * s * e(t),
                      pan=lambda t: (d * 70 * s * (1 - 2 * e(t)), 0.0))
    if motion == "drift":
        return Camera(zoom=lambda t: 1.0 + 0.035 * s * e(t) + 0.03 * s,
                      pan=lambda t: (-45 * s * (e(t) - 0.5), -12 * s * (e(t) - 0.5)),
                      rot=lambda t: 0.5 * s * (e(t) - 0.5))
    if motion == "none":
        return Camera()
    # push (default)
    return Camera(zoom=lambda t: 1.0 + 0.08 * s * e(t))


# --------------------------------------------------------------------------------------- backgrounds
def bg_layers(bg: dict | None, seed: int, ctx) -> list[Layer]:
    bg = bg or {"type": "paper"}
    if bg.get("type") == "photo":
        rgb = load_rgb(bg["src"])
        cw, ch = int(W * 1.06), int(H * 1.06)
        c = im.cover(rgb, cw, ch, tuple(bg.get("focus", (0.5, 0.5))))
        style = bg.get("style", "halftone")
        toned = im.tone(c, style, cell=5.5, seed=seed)
        # mute a touch so cut-outs with white borders pop
        mute = float(bg.get("mute", 0.12))
        toned = (toned.astype(np.float32) * (1 - mute) + np.array(im.PAPER, np.float32) * mute * 0.8).astype(np.uint8)
        return [Layer(im.rgb_sprite(toned), W / 2, H / 2, depth=0.55)]
    color = im.hex_rgb(bg["color"]) if bg.get("color") else im.PAPER
    p = im.paper(int(W * 1.08), int(H * 1.08), color, seed=seed % 5)
    return [Layer(im.rgb_sprite(p), W / 2, H / 2, depth=0.4)]


def cutout_layer(photo, ctx, target_h, x, bottom=True, y=None, style="halftone", subject="largest",
                 max_w=None, anim=None, seed=1, depth=1.0, rot=0.0, border=16) -> Layer:
    rgb = load_rgb(photo)
    mask = cutout_mask(photo, ctx["cache"], subject=subject)
    sp = im.sticker(rgb, mask, int(target_h), border=border, style=style, seed=seed, max_w=max_w)
    if y is None:
        # anchor so the subject's bottom sits just past the frame bottom (like a print cropped by the frame)
        y = H - sp.h / 2 + 60 if bottom else H / 2
    return Layer(sp, x, y, rot=rot, depth=depth, anim=anim, jitter=0.0, seed=seed)


def caption_layers(cap: dict, x: float, y: float, seed: int, start=0.45, max_w=760, align="left",
                   size=78, sub_size=44) -> list[Layer]:
    """Big typewriter strip + optional smaller sub strip + a tape piece (ref: 'PARNELLI JONES')."""
    layers = []
    text = cap.get("text", "").upper()
    main = im.strip(im.wrap(text, "typewriter", size, max_w), size=size, seed=seed, pad=(46, 26))
    sub = None
    if cap.get("sub"):
        sub = im.strip(im.wrap(cap["sub"].upper(), "typewriter", sub_size, max_w), size=sub_size,
                       seed=seed + 1, pad=(30, 14))
    rot_main = -1.2 + (seed % 5) * 0.3
    mx = x + (main.w / 2 if align == "left" else -main.w / 2 if align == "right" else 0)
    layers.append(Layer(main, mx, y, rot=rot_main, depth=1.1, anim=pop_in(start, from_scale=0.7, from_rot=-5),
                        jitter=0.6, seed=seed))
    tp = im.tape(170, 54, seed=seed + 2)
    tx = mx - main.w / 2 + 40
    layers.append(Layer(tp, tx, y - main.h / 2 + 22, rot=24, depth=1.1, anim=pop_in(start + 0.25, dur=0.25, from_scale=0.9, from_rot=0),
                        jitter=0.6, seed=seed + 3))
    if sub is not None:
        sx = x + (sub.w / 2 + 40 if align == "left" else -sub.w / 2 - 40 if align == "right" else 0)
        layers.append(Layer(sub, sx, y + main.h / 2 + sub.h / 2 - 6, rot=1.5, depth=1.15,
                            anim=pop_in(start + 0.3, from_scale=0.7, from_rot=6), jitter=0.6, seed=seed + 4))
    return layers


def number_card_layers(number: str, label: str | None, accent, x, y, seed, start=0.25, max_num_h=330,
                       card_min_w=560, depth=1.2, label_start=None) -> list[Layer]:
    num = str(number)
    size = int(max_num_h * 1.38)
    m = im.text_mask(num, "numeral", size)
    # keep long numbers (e.g. "1985") from making a giant card
    max_text_w = 980
    if m.shape[1] > max_text_w:
        f = max_text_w / m.shape[1]
        m = cv2.resize(m, (int(m.shape[1] * f), int(m.shape[0] * f)), interpolation=cv2.INTER_AREA)
    m = im.grainy(m, seed=seed)
    th, tw = m.shape
    lab = im.strip(label.upper(), size=54, seed=seed + 5, pad=(40, 16)) if label else None
    cw = max(card_min_w, tw + 150, (lab.w + 90) if lab else 0)
    ch = th + 110 + (lab.h + 10 if lab else 0)
    cd = im.card(cw, ch, accent, seed=seed)
    ink = im.ink_sprite(m, im.NUMERAL)
    pad = 12
    im.over(cd, ink, pad + (cw - tw) // 2, pad + 55)
    cd = im.with_shadow(cd, offset=(8, 12), blur=14, opacity=0.45)
    out = [Layer(cd, x, y, rot=-2.0 + (seed % 3) * 0.8, depth=depth,
                 anim=pop_in(start, dur=0.45, from_scale=1.35, from_rot=-7), jitter=0.7, seed=seed)]
    if lab is not None:
        ly = y + ch / 2 - lab.h / 2 - 22
        out.append(Layer(lab, x + 10, ly, rot=-1.5, depth=depth + 0.05,
                         anim=pop_in(label_start if label_start is not None else start + 0.35, from_scale=0.7, from_rot=5),
                         jitter=0.7, seed=seed + 1))
    return out


def _photo_frame_source(spec, ctx, cw, ch, style, seed):
    """Return (sprite, frame_fn) for a full-frame photo or a video clip."""
    if spec.get("video"):
        return None, _video_frames(spec, ctx, cw, ch, style, seed)
    rgb = load_rgb(spec["photo"])
    c = im.cover(rgb, cw, ch, tuple(spec.get("focus", (0.5, 0.5))))
    return im.rgb_sprite(im.tone(c, style, cell=6.0, seed=seed)), None


def _video_frames(spec, ctx, cw, ch, style, seed):
    dur = float(spec["duration"])
    n = int(round(dur * FPS))
    path = Path(ctx["workdir"]) / f"video_{seed}.u8"
    vf = (f"fps={FPS},scale={cw}:{ch}:force_original_aspect_ratio=increase:flags=bicubic,"
          f"crop={cw}:{ch}")
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(float(spec.get("in", 0))), "-i", str(spec["video"]),
           "-t", f"{dur + 0.5:.3f}", "-vf", vf, "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", str(path)]
    subprocess.run(cmd, check=True)
    fsz = cw * ch * 3
    got = max(1, path.stat().st_size // fsz)
    mm = np.memmap(path, np.uint8, "r", shape=(got, ch, cw, 3))

    def frame(i):
        fr = np.asarray(mm[min(i, got - 1)])
        return im.rgb_sprite(im.tone(fr, style, cell=6.0, seed=seed + (i // 2) % 4))
    return frame


# --------------------------------------------------------------------------------------- scene types
def build_cutout(spec, ctx) -> Scene:
    dur = float(spec["duration"])
    sd = _seed(spec)
    pos = spec.get("position", "right")
    layers = bg_layers(spec.get("bg"), sd, ctx)
    cap = spec.get("caption")
    scale = float(spec.get("scale", 1.0))
    target_h = H * 0.98 * scale
    if pos == "center":
        x = W / 2
        max_w = W * 0.8
    else:
        max_w = W * (0.62 if cap else 0.75)
        x = None
    lay = cutout_layer(spec["photo"], ctx, target_h, 0, style=spec.get("style", "halftone"),
                       subject=spec.get("subject", "largest"), max_w=max_w, seed=sd % 97,
                       anim=slide_in(0.0, frm=(0, 180), dur=0.55, rot=2.5))
    if x is None:
        # push the sticker against its side, bleeding slightly off-frame like the reference
        x = W - lay.sprite.w / 2 + 40 if pos == "right" else lay.sprite.w / 2 - 40
        x = max(x, W * 0.62) if pos == "right" else min(x, W * 0.38)
    lay.x = x
    lay.anim = drift(vx=(-6 if pos == "right" else 6), base=lay.anim)
    layers.append(lay)
    if cap:
        if pos == "right":
            layers += caption_layers(cap, 90, H * 0.66, sd, align="left", max_w=max(420, int(W - lay.sprite.w - 160)))
        elif pos == "left":
            layers += caption_layers(cap, W - 90, H * 0.66, sd, align="right", max_w=max(420, int(W - lay.sprite.w - 160)))
        else:
            layers += caption_layers(cap, W / 2, H * 0.82, sd, align="center", max_w=1100)
    return Scene(layers, dur, camera_for(spec.get("motion", "push"), dur))


def build_number_card(spec, ctx) -> Scene:
    dur = float(spec["duration"])
    sd = _seed(spec)
    accent = im.hex_rgb(spec.get("accent", DEFAULT_ACCENT))
    layers = bg_layers(spec.get("bg"), sd, ctx)
    photo = spec.get("photo")
    ppos = spec.get("photo_position", "right")
    cx, cy = W / 2, H * 0.45
    if photo:
        lay = cutout_layer(photo, ctx, H * 0.98, 0, style=spec.get("style", "halftone"),
                           subject=spec.get("subject", "largest"), max_w=W * 0.55, seed=sd % 97,
                           anim=slide_in(0.0, frm=(0, 160), dur=0.55, rot=2.0))
        lay.x = W - lay.sprite.w / 2 + 40 if ppos == "right" else lay.sprite.w / 2 - 40
        lay.x = max(lay.x, W * 0.64) if ppos == "right" else min(lay.x, W * 0.36)
        lay.anim = drift(vx=(-6 if ppos == "right" else 6), base=lay.anim)
        layers.append(lay)
        cx = W * 0.27 if ppos == "right" else W * 0.73
        cy = H * 0.40
    cards = number_card_layers(spec["number"], spec.get("label"), accent, cx, cy, sd)
    layers += cards
    if spec.get("sub"):
        card_h = cards[0].sprite.h
        layers += caption_layers({"text": spec["sub"]}, cx - 300, cy + card_h / 2 + 20, sd + 11, start=0.85,
                                 align="left", max_w=900, size=76)
    return Scene(layers, dur, camera_for(spec.get("motion", "push"), dur))


def build_full(spec, ctx) -> Scene:
    dur = float(spec["duration"])
    sd = _seed(spec)
    style = spec.get("style", "halftone")
    motion = spec.get("motion", "push")
    sp, fn = _photo_frame_source(spec, ctx, W, H, style, sd)
    layers = [Layer(sp, W / 2, H / 2, depth=1.0, frame_fn=fn)]
    if sp is None:
        layers[0].sprite = fn(0)
    cap = spec.get("caption")
    if cap:
        if isinstance(cap, str):
            cap = {"text": cap}
        layers += caption_layers(cap, 110, H * 0.80, sd, start=0.5, align="left", max_w=900, size=68)
        for L in layers[1:]:
            L.depth = 0.0  # captions sit on the "glass", not in the photo
    return Scene(layers, dur, camera_for(motion, dur), grain=0.03)


def _sheet_with_lines(text_lines, x, y, seed, kind="typewriter", size=58, color=im.STRIP, rot=-1.0,
                      start=0.4, cps=38.0, pad=(80, 70), depth=1.0, line_gap=1.28):
    """Paper sheet + one ink layer per line with a typewriter left-to-right reveal."""
    masks = [im.text_mask(ln, kind, size) if ln.strip() else np.zeros((1, 1), np.float32) for ln in text_lines]
    lh = int(size * line_gap)
    tw = max(m.shape[1] for m in masks)
    sw, sh = tw + 2 * pad[0], lh * len(masks) + 2 * pad[1] - (lh - size)
    a = im.torn_mask(sw, sh, seed, rough=2.5, pad=10, step=5)
    hh, ww = a.shape
    col = np.array(color, np.float32)[None, None]
    rgb = col * (1 + 0.05 * (im.fbm(hh, ww, seed + 3, (160, 20, 3), (1, 0.5, 0.4)) - 0.5))[..., None]
    sheet = im.with_shadow(im.Sprite.from_rgba(np.clip(rgb, 0, 255), a), offset=(6, 10), blur=12, opacity=0.4)
    layers = [Layer(sheet, x, y, rot=rot, depth=depth, anim=pop_in(start - 0.3, from_scale=0.8, from_rot=4),
                    jitter=0.5, seed=seed)]
    t = start + 0.2
    r = math.radians(-rot)
    for i, m in enumerate(masks):
        inkv = 0.8 + 0.2 * im.fbm(m.shape[0], m.shape[1], seed + 10 + i, (6, 2), (1, 0.8))
        sp = im.ink_sprite(np.clip(m * 1.15, 0, 1) * inkv, im.INK)
        # line centre in sheet coordinates (left aligned)
        lx = -sw / 2 + pad[0] + m.shape[1] / 2
        ly = -sh / 2 + pad[1] + i * lh + size * 0.5
        X = x + lx * math.cos(r) - ly * math.sin(r)
        Y = y + lx * math.sin(r) + ly * math.cos(r)
        n = max(1, len(text_lines[i]))
        d = n / cps
        t0 = t

        def rev(tt, t0=t0, d=d, n=n):
            k = math.floor(clamp01((tt - t0) / d) * n + 1e-6)
            return k / n
        layers.append(Layer(sp, X, Y, rot=rot, depth=depth, anim=lambda tt, t0=t0: State(alpha=1.0 if tt >= t0 else 0.0),
                            reveal=rev, jitter=0.5, seed=seed))  # same seed => boils with the sheet
        t += d + 0.12
    return layers, sheet, t


def build_quote(spec, ctx) -> Scene:
    dur = float(spec["duration"])
    sd = _seed(spec)
    accent = im.hex_rgb(spec.get("accent", DEFAULT_ACCENT))
    layers = bg_layers(spec.get("bg"), sd, ctx)
    photo = spec.get("photo")
    text = "“" + spec["text"].strip().strip('"“”') + "”"
    if photo:
        lay = cutout_layer(photo, ctx, H * 0.9, 0, style=spec.get("style", "halftone"),
                           subject=spec.get("subject", "largest"), max_w=W * 0.4, seed=sd % 97,
                           anim=slide_in(0.0, frm=(-160, 0), dur=0.55, rot=-2.0))
        lay.x = min(lay.sprite.w / 2 - 30, W * 0.3)
        lay.anim = drift(vx=5, base=lay.anim)
        layers.append(lay)
        tx, maxw = W * 0.64, 900
    else:
        tx, maxw = W / 2, 1300
    size = 60 if len(text) < 140 else 50
    lines = im.wrap(text, "typewriter", size, maxw).split("\n")
    # typing speed adapts so the quote finishes by ~65% of the scene
    total_chars = sum(len(l) for l in lines)
    cps = max(30.0, total_chars / max(0.8, dur * 0.65 - 0.6))
    ty = H * 0.46
    ql, sheet, t_end = _sheet_with_lines(lines, tx, ty, sd, size=size, start=0.45, cps=cps, rot=-1.0)
    layers += ql
    # accent card with a big grainy quote mark, stuck on the sheet's top-left corner
    qm = im.grainy(im.text_mask("“", "display_serif", 300), seed=sd)
    qcard = im.card(qm.shape[1] + 90, qm.shape[0] + 60, accent, seed=sd + 7)
    im.over(qcard, im.ink_sprite(qm, im.NUMERAL), 12 + 45, 12 + 45)
    qcard = im.with_shadow(qcard, offset=(6, 10), blur=10, opacity=0.4)
    layers.append(Layer(qcard, tx - sheet.w / 2 + 40, ty - sheet.h / 2 + 30, rot=-6, depth=1.15,
                        anim=pop_in(0.3, from_scale=1.4, from_rot=-10), jitter=0.6, seed=sd + 1))
    if spec.get("attribution"):
        att = im.strip("— " + spec["attribution"].upper(), size=44, seed=sd + 5, pad=(30, 14))
        layers.append(Layer(att, tx + sheet.w / 2 - att.w / 2 - 20, ty + sheet.h / 2 + 10, rot=1.8, depth=1.2,
                            anim=pop_in(min(t_end, dur - 1.0), from_scale=0.7, from_rot=6), jitter=0.6, seed=sd + 2))
    tp = im.tape(200, 58, seed=sd + 3)
    layers.append(Layer(tp, tx + sheet.w / 2 - 40, ty - sheet.h / 2 + 10, rot=38, depth=1.05,
                        anim=pop_in(0.35, dur=0.25, from_scale=0.9, from_rot=0), jitter=0.5, seed=sd))
    return Scene(layers, dur, camera_for(spec.get("motion", "drift"), dur))


def _clipping(spec, seed, accent):
    """Newspaper clipping sprite: masthead, date rules, headline, greeked columns."""
    cw, ch = 1260, 820
    news = (236, 229, 212)
    a = im.torn_mask(cw, ch, seed, rough=7.0, pad=14, step=4)
    hh, ww = a.shape
    tex = im.fbm(hh, ww, seed + 1, (200, 40, 4), (1, 0.4, 0.4))
    rgb = np.array(news, np.float32)[None, None] * (0.95 + 0.07 * tex)[..., None]
    # yellowed, darker edges
    edge = cv2.GaussianBlur(a, (0, 0), 30)
    rgb *= (0.86 + 0.14 * np.clip(edge * 1.6, 0, 1))[..., None] * np.array([1.0, 0.98, 0.93])[None, None]
    ox, oy = 14 + 70, 14 + 40
    inner_w = cw - 140
    canvas = im.Sprite.from_rgba(np.clip(rgb, 0, 255), a)

    def ink(mask, x, y, col=im.INK, alpha=1.0):
        inkv = 0.85 + 0.15 * im.fbm(mask.shape[0], mask.shape[1], seed + x % 97, (5, 2), (1, 0.8))
        im.over(canvas, im.ink_sprite(np.clip(mask * 1.1, 0, 1) * inkv * alpha, col), int(x), int(y))

    pub = spec.get("publication", "The Daily Record")
    mh = im.text_mask(pub, "blackletter", 110)
    if mh.shape[1] > inner_w:
        f = inner_w / mh.shape[1]
        mh = cv2.resize(mh, (int(mh.shape[1] * f), int(mh.shape[0] * f)), interpolation=cv2.INTER_AREA)
    ink(mh, ox + (inner_w - mh.shape[1]) // 2, oy)
    y = oy + mh.shape[0] + 22
    rule = np.ones((3, inner_w), np.float32)
    ink(rule, ox, y)
    ink(np.ones((1, inner_w), np.float32), ox, y + 6)
    date = spec.get("date", "")
    if date:
        dm = im.text_mask(date.upper(), "serif", 26, tracking=3)
        ink(dm, ox + (inner_w - dm.shape[1]) // 2, y + 16)
        y += 16 + dm.shape[0] + 14
    else:
        y += 20
    ink(np.ones((1, inner_w), np.float32), ox, y)
    ink(np.ones((3, inner_w), np.float32), ox, y + 4)
    y += 30
    # headline: fit to width, up to 3 lines
    head = spec["text"].upper()
    size = 120
    while size > 50:
        wrapped = im.wrap(head, "serif_bold", size, inner_w)
        if wrapped.count("\n") < 3 and all(im.font("serif_bold", size).getlength(l) <= inner_w for l in wrapped.split("\n")):
            break
        size -= 6
    hm = im.text_mask(wrapped, "serif_bold", size, line_gap=1.02, align="center")
    ink(hm, ox + (inner_w - hm.shape[1]) // 2, y)
    head_box = (ox + (inner_w - hm.shape[1]) // 2, y, hm.shape[1], hm.shape[0])
    y += hm.shape[0] + 30
    ink(np.ones((2, inner_w), np.float32), ox, y)
    y += 20
    # greeked body copy: 3 columns of grey word-bars
    rng = np.random.default_rng(seed)
    colw = (inner_w - 2 * 30) // 3
    for c in range(3):
        x0 = ox + c * (colw + 30)
        yy = y
        while yy < ch + 14 - 20:
            xx = x0
            last = rng.random() < 0.12
            end = x0 + (colw * rng.uniform(0.3, 0.8) if last else colw)
            while xx < end - 12:
                wl = int(rng.uniform(18, 70))
                wl = min(wl, int(end - xx))
                if wl > 4:
                    bar = np.ones((9, wl), np.float32) * 0.55
                    ink(bar, xx, yy, alpha=0.9)
                xx += wl + 9
            yy += 19
        if c < 2:
            ink(np.ones((ch - y, 1), np.float32) * 0.6, x0 + colw + 15, y)
    return canvas, head_box


def build_headline(spec, ctx) -> Scene:
    dur = float(spec["duration"])
    sd = _seed(spec)
    accent = im.hex_rgb(spec.get("accent", DEFAULT_ACCENT))
    layers = bg_layers(spec.get("bg"), sd, ctx)
    clip, (hx, hy, hw, hh) = _clipping(spec, sd, accent)
    clip_s = im.with_shadow(clip, offset=(10, 16), blur=18, opacity=0.45)
    pad = (clip_s.w - clip.w) // 2
    cx, cy = W / 2, H / 2 + 60
    rot = -2.2
    layers.append(Layer(clip_s, cx, cy, rot=rot, depth=1.0,
                        anim=slide_in(0.0, frm=(40, 700), dur=0.7, rot=-6), jitter=0.5, seed=sd))
    # accent marker stroke under the headline, drawn left to right
    sw_, sh_ = int(hw * 1.04), 22
    a = im.torn_mask(sw_, sh_, sd + 9, rough=3.0, pad=6, step=6)
    stroke = im.Sprite.from_rgba(np.broadcast_to(np.array(accent, np.uint8)[None, None], a.shape + (3,)), a * 0.92)
    # headline box centre relative to clipping centre
    lx = pad + hx + hw / 2 - clip_s.w / 2
    ly = pad + hy + hh + 14 - clip_s.h / 2
    r = math.radians(-rot)
    X = cx + lx * math.cos(r) - ly * math.sin(r)
    Y = cy + lx * math.sin(r) + ly * math.cos(r)
    t0 = 1.0
    layers.append(Layer(stroke, X, Y, rot=rot, depth=1.0,
                        anim=lambda t: State(alpha=1.0 if t >= t0 else 0.0),
                        reveal=lambda t: ease_out_cubic((stepped(t, 12) - t0) / 0.5), jitter=0.5, seed=sd))
    tp = im.tape(220, 60, seed=sd + 1)
    layers.append(Layer(tp, cx - 40, cy - clip.h / 2 + 6, rot=-4, depth=1.05,
                        anim=pop_in(0.65, dur=0.25, from_scale=0.9, from_rot=0), jitter=0.5, seed=sd))
    return Scene(layers, dur, camera_for(spec.get("motion", "push"), dur))


def build_title(spec, ctx) -> Scene:
    dur = float(spec["duration"])
    sd = _seed(spec)
    accent = im.hex_rgb(spec.get("accent", DEFAULT_ACCENT))
    layers = bg_layers(spec.get("bg"), sd, ctx)
    # kicker: accent card with grainy dark numerals-style text
    kick = spec.get("kicker", "")
    title = spec["title"].upper()
    size = 112 if len(title) < 28 else 92
    lines = im.wrap(title, "typewriter", size, 1500).split("\n")
    strips = [im.strip(ln, size=size, seed=sd + 20 + i, pad=(54, 30)) for i, ln in enumerate(lines)]
    total_h = sum(s.h for s in strips) - 20 * (len(strips) - 1)
    kick_h = 0
    kcard = None
    if kick:
        km = im.grainy(im.text_mask(kick.upper(), "numeral", 84, tracking=4), seed=sd, holes=0.22)
        kcard = im.card(km.shape[1] + 110, km.shape[0] + 80, accent, seed=sd + 3)
        im.over(kcard, im.ink_sprite(km, im.NUMERAL), 12 + 55, 12 + 40)
        kcard = im.with_shadow(kcard, offset=(6, 10), blur=12, opacity=0.45)
        kick_h = kcard.h - 40
    y = H / 2 - (total_h + kick_h) / 2
    if kcard is not None:
        layers.append(Layer(kcard, W / 2 - 40, y + kcard.h / 2, rot=-3.0, depth=1.15,
                            anim=pop_in(0.2, from_scale=1.4, from_rot=-9), jitter=0.7, seed=sd))
        y += kick_h
    rots = [1.2, -1.4, 0.8, -0.6]
    offs = [30, -40, 20, -10]
    for i, s in enumerate(strips):
        layers.append(Layer(s, W / 2 + offs[i % 4], y + s.h / 2, rot=rots[i % 4], depth=1.05 + 0.03 * i,
                            anim=pop_in(0.55 + 0.22 * i, from_scale=0.7, from_rot=5 * (-1) ** i),
                            jitter=0.6, seed=sd + 30 + i))
        y += s.h - 20
    first = strips[0]
    tp = im.tape(180, 56, seed=sd + 4)
    fy = H / 2 - (total_h + kick_h) / 2 + kick_h + first.h / 2
    layers.append(Layer(tp, W / 2 + offs[0] + first.w / 2 - 30, fy - first.h / 2 + 12, rot=-30, depth=1.1,
                        anim=pop_in(0.85, dur=0.25, from_scale=0.9, from_rot=0), jitter=0.6, seed=sd + 5))
    return Scene(layers, dur, camera_for(spec.get("motion", "push"), dur, strength=0.7))


BUILDERS = {
    "cutout": build_cutout,
    "number_card": build_number_card,
    "full": build_full,
    "quote": build_quote,
    "headline": build_headline,
    "title": build_title,
}


def build(spec: dict, ctx) -> Scene:
    kind = spec.get("type")
    if kind not in BUILDERS:
        raise ValueError(f"unknown scene type {kind!r}; expected one of {sorted(BUILDERS)}")
    sc = BUILDERS[kind](spec, ctx)
    sc.fade_in = float(spec.get("fade_in", 0.0))
    sc.fade_out = float(spec.get("fade_out", 0.0))
    if "grain" in spec:
        sc.grain = float(spec["grain"])
    if spec.get("stop_motion") is False:
        for L in sc.layers:
            L.jitter = 0.0
    return sc
