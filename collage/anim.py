"""Tiny deterministic 2.5D compositor: sprite layers + a camera, rendered frame by frame.

A frame is a pure function of the frame index, so rendering is deterministic and parallel.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from .imaging import H, W, Sprite

FPS = 30


# --------------------------------------------------------------------------------------- easing
def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out_cubic(x):
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def ease_out_back(x, k=1.9):
    x = clamp01(x)
    return 1 + (k + 1) * (x - 1) ** 3 + k * (x - 1) ** 2


def stepped(t, fps_step=12):
    """Quantise time to `fps_step` updates per second (stop-motion "on twos/threes")."""
    return math.floor(t * fps_step + 1e-6) / fps_step


# --------------------------------------------------------------------------------------- layers
@dataclass
class State:
    dx: float = 0.0
    dy: float = 0.0
    scale: float = 1.0
    rot: float = 0.0       # degrees, counter-clockwise positive (screen)
    alpha: float = 1.0


Anim = Callable[[float], State]


@dataclass
class Layer:
    sprite: Sprite
    x: float                  # centre position on the 1920x1080 frame
    y: float
    rot: float = 0.0
    scale: float = 1.0
    depth: float = 1.0        # camera parallax factor (0 = fixed, 1 = moves with camera)
    anim: Anim | None = None
    jitter: float = 0.0       # stop-motion boil amplitude (px)
    seed: int = 0
    frame_fn: Callable[[int], Sprite] | None = None   # per-frame sprite (video)
    reveal: Callable[[float], float] | None = None    # 0..1 left-to-right wipe (typewriter)


@dataclass
class Camera:
    zoom: Callable[[float], float] = lambda t: 1.0
    pan: Callable[[float], tuple[float, float]] = lambda t: (0.0, 0.0)
    rot: Callable[[float], float] = lambda t: 0.0


@dataclass
class Scene:
    layers: list[Layer]
    duration: float
    camera: Camera = field(default_factory=Camera)
    grain: float = 0.035
    stop_motion_fps: int = 12
    fade_in: float = 0.0
    fade_out: float = 0.0


def _matrix(layer: Layer, st: State, cam: Camera, t: float, sprite: Sprite, boil) -> np.ndarray:
    z = cam.zoom(t)
    zl = 1.0 + (z - 1.0) * layer.depth
    px, py = cam.pan(t)
    cr = cam.rot(t) * layer.depth
    s = layer.scale * st.scale * zl
    r = math.radians(-(layer.rot + st.rot + boil[2] + cr))
    c, sn = math.cos(r) * s, math.sin(r) * s
    cx, cy = sprite.w / 2.0, sprite.h / 2.0
    # position after camera: C + zl * (p - C) + pan*depth
    X = W / 2 + zl * (layer.x + st.dx + boil[0] - W / 2) + px * layer.depth
    Y = H / 2 + zl * (layer.y + st.dy + boil[1] - H / 2) + py * layer.depth
    if cr:
        a = math.radians(-cr)
        ox, oy = X - W / 2, Y - H / 2
        X = W / 2 + ox * math.cos(a) - oy * math.sin(a)
        Y = H / 2 + ox * math.sin(a) + oy * math.cos(a)
    return np.array([[c, -sn, X - (c * cx - sn * cy)],
                     [sn, c, Y - (sn * cx + c * cy)]], np.float32)


def _boil(layer: Layer, t: float, fps_step: int):
    if not layer.jitter:
        return (0.0, 0.0, 0.0)
    k = int(math.floor(t * fps_step / 2))  # new pose ~6x per second
    rng = np.random.default_rng(layer.seed * 7919 + k)
    j = layer.jitter
    return (float(rng.uniform(-j, j)), float(rng.uniform(-j, j)), float(rng.uniform(-0.12, 0.12) * j))


def draw(canvas: np.ndarray, sp: Sprite, M: np.ndarray, alpha: float = 1.0, reveal: float = 1.0):
    """Warp premultiplied sprite with affine M (src->dst) and composite onto float canvas (H,W,3)."""
    if alpha <= 0.001:
        return
    if sp.px is None:
        _draw_opaque(canvas, sp.u8, M, alpha)
        return
    src = sp.px
    if reveal < 1.0:
        cut = int(round(sp.w * clamp01(reveal)))
        if cut <= 0:
            return
        src = src[:, :cut]
    h, w = src.shape[:2]
    corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], np.float32) @ M.T
    x0 = max(0, int(math.floor(corners[:, 0].min())) - 1)
    y0 = max(0, int(math.floor(corners[:, 1].min())) - 1)
    x1 = min(W, int(math.ceil(corners[:, 0].max())) + 1)
    y1 = min(H, int(math.ceil(corners[:, 1].max())) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    M2 = M.copy()
    M2[0, 2] -= x0
    M2[1, 2] -= y0
    # pure integer translation -> slice instead of resampling
    if abs(M2[0, 0] - 1) < 1e-6 and abs(M2[1, 1] - 1) < 1e-6 and abs(M2[0, 1]) < 1e-6 \
            and abs(M2[0, 2] - round(M2[0, 2])) < 1e-6 and abs(M2[1, 2] - round(M2[1, 2])) < 1e-6:
        ox, oy = int(round(M2[0, 2])), int(round(M2[1, 2]))
        warped = np.zeros((y1 - y0, x1 - x0, 4), np.float32)
        sx0, sy0 = max(0, -ox), max(0, -oy)
        dx0, dy0 = max(0, ox), max(0, oy)
        ww = min(w - sx0, warped.shape[1] - dx0)
        hh = min(h - sy0, warped.shape[0] - dy0)
        if ww > 0 and hh > 0:
            warped[dy0:dy0 + hh, dx0:dx0 + ww] = src[sy0:sy0 + hh, sx0:sx0 + ww]
    else:
        scale = math.hypot(M[0, 0], M[1, 0])
        interp = cv2.INTER_AREA if scale < 0.7 else cv2.INTER_LINEAR
        if interp == cv2.INTER_AREA:
            interp = cv2.INTER_LINEAR  # warpAffine has no true AREA; pre-shrink below
            if scale < 0.5:
                f = scale * 1.5
                src = cv2.resize(src, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA)
                M2[:, :2] /= f
        warped = cv2.warpAffine(src, M2, (x1 - x0, y1 - y0), flags=interp,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    if alpha < 1.0:
        warped *= alpha
    reg = canvas[y0:y1, x0:x1]
    reg *= (1.0 - warped[..., 3:4])
    reg += warped[..., :3]


def _draw_opaque(canvas, u8, M, alpha):
    """Opaque RGB uint8 layer: warp in uint8 (3x cheaper than float RGBA), coverage from a warped mask."""
    h, w = u8.shape[:2]
    rgb = cv2.warpAffine(u8, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], np.float32) @ M.T
    full = corners[:, 0].min() <= 0 and corners[:, 1].min() <= 0 and corners[:, 0].max() >= W and corners[:, 1].max() >= H
    covers = full and abs(M[0, 1]) < 1e-9  # axis aligned and covering the frame
    f = rgb.astype(np.float32) * (1.0 / 255.0)
    if covers and alpha >= 0.999:
        canvas[:] = f
        return
    m = cv2.warpAffine(np.full((h, w), 255, np.uint8), M, (W, H), flags=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=0).astype(np.float32) * (alpha / 255.0)
    m = m[..., None]
    canvas *= (1.0 - m)
    canvas += f * m


class Grain:
    def __init__(self, amount: float, n=6, seed=99):
        self.amount = amount
        self.tiles = []
        if amount <= 0:
            return
        rng = np.random.default_rng(seed)
        for _ in range(n):
            g = rng.normal(0, 1, (H // 2, W // 2)).astype(np.float32)
            g = cv2.resize(g, (W, H), interpolation=cv2.INTER_LINEAR)
            self.tiles.append((g * amount)[..., None])

    def apply(self, canvas, frame):
        if self.tiles:
            canvas += self.tiles[(frame // 2) % len(self.tiles)]


def render_frame(scene: Scene, i: int, grain: Grain | None = None) -> np.ndarray:
    t = i / FPS
    canvas = np.zeros((H, W, 3), np.float32)
    for layer in scene.layers:
        st = layer.anim(t) if layer.anim else State()
        if st.alpha <= 0.001:
            continue
        sp = layer.frame_fn(i) if layer.frame_fn else layer.sprite
        M = _matrix(layer, st, scene.camera, t, sp, _boil(layer, t, scene.stop_motion_fps))
        rv = layer.reveal(t) if layer.reveal else 1.0
        draw(canvas, sp, M, st.alpha, rv)
    if grain:
        grain.apply(canvas, i)
    if scene.fade_in and t < scene.fade_in:
        canvas *= t / scene.fade_in
    if scene.fade_out and t > scene.duration - scene.fade_out:
        canvas *= max(0.0, (scene.duration - t) / scene.fade_out)
    return (np.clip(canvas, 0, 1) * 255 + 0.5).astype(np.uint8)


# --------------------------------------------------------------------------------------- entrance anims
def pop_in(start: float, dur=0.45, from_scale=0.55, from_rot=8.0, step_fps: int | None = 12,
           exit_at: float | None = None) -> Anim:
    """Stop-motion "slap down": grows with overshoot, rotates into place."""
    def f(t):
        tt = stepped(t, step_fps) if step_fps else t
        if tt < start:
            return State(alpha=0.0)
        x = (tt - start) / dur
        e = ease_out_back(x, 2.2)
        st = State(scale=from_scale + (1 - from_scale) * e, rot=from_rot * (1 - ease_out_cubic(x)),
                   alpha=clamp01(x * 4))
        if exit_at is not None and t > exit_at:
            st.alpha *= clamp01(1 - (t - exit_at) / 0.25)
        return st
    return f


def slide_in(start: float, frm=(0, 300), dur=0.6, rot=0.0, step_fps: int | None = 12, k=1.4) -> Anim:
    def f(t):
        tt = stepped(t, step_fps) if step_fps else t
        if tt < start:
            return State(alpha=0.0)
        x = (tt - start) / dur
        e = ease_out_back(x, k)
        return State(dx=frm[0] * (1 - e), dy=frm[1] * (1 - e), rot=rot * (1 - e), alpha=clamp01(x * 5))
    return f


def fade_in(start: float, dur=0.4) -> Anim:
    return lambda t: State(alpha=ease_in_out((t - start) / dur)) if t >= start else State(alpha=0.0)


def drift(vx=0.0, vy=0.0, vs=0.0, base: Anim | None = None) -> Anim:
    """Constant slow drift (px/s, scale/s) layered on top of another anim (parallax float)."""
    def f(t):
        st = base(t) if base else State()
        st.dx += vx * t
        st.dy += vy * t
        st.scale *= 1 + vs * t
        return st
    return f
