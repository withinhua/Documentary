"""Tiny deterministic 2.5D compositor: sprite layers + a camera, rendered frame by frame.

A frame is a pure function of the frame index, so rendering is deterministic and parallel.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
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


def _bounds(M: np.ndarray, w: int, h: int):
    """Integer destination rect (x0, y0, x1, y1) covering the warped w x h sprite, clipped to the frame."""
    c = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], np.float32) @ M.T
    x0 = max(0, int(math.floor(c[:, 0].min())) - 1)
    y0 = max(0, int(math.floor(c[:, 1].min())) - 1)
    x1 = min(W, int(math.ceil(c[:, 0].max())) + 1)
    y1 = min(H, int(math.ceil(c[:, 1].max())) + 1)
    return x0, y0, x1, y1


def _blend(reg: np.ndarray, src: np.ndarray, alpha: float = 1.0) -> None:
    """reg = reg * (1 - a) + src for premultiplied RGBA uint8 `src` (in place, saturating uint8 SIMD ops)."""
    if alpha < 0.999:
        src = cv2.convertScaleAbs(src, alpha=alpha)
    inv = cv2.bitwise_not(cv2.extractChannel(src, 3))
    cv2.multiply(reg, cv2.merge((inv, inv, inv, inv)), dst=reg, scale=1.0 / 255.0)
    cv2.add(reg, src, dst=reg)


def draw(canvas: np.ndarray, sp: Sprite, M: np.ndarray, alpha: float = 1.0, reveal: float = 1.0):
    """Warp a sprite with affine M (src->dst) and composite it onto the RGBA uint8 canvas (H, W, 4).

    Only the sprite's destination rectangle is touched ("dirty rect"); sprites are premultiplied
    RGBA uint8 (opaque photos/backgrounds get alpha 255), so the warp takes OpenCV's 4-channel SIMD path.
    """
    if alpha <= 0.001:
        return
    opaque = sp.px is None
    src = sp.pm8()
    if reveal < 1.0 and not opaque:
        cut = int(round(sp.w * clamp01(reveal)))
        if cut <= 0:
            return
        src = src[:, :cut]
    h, w = src.shape[:2]
    x0, y0, x1, y1 = _bounds(M, w, h)
    if x1 <= x0 or y1 <= y0:
        return
    M2 = M.copy()
    M2[0, 2] -= x0
    M2[1, 2] -= y0
    # pure integer translation -> slice instead of resampling
    if abs(M2[0, 0] - 1) < 1e-6 and abs(M2[1, 1] - 1) < 1e-6 and abs(M2[0, 1]) < 1e-6 \
            and abs(M2[0, 2] - round(M2[0, 2])) < 1e-6 and abs(M2[1, 2] - round(M2[1, 2])) < 1e-6:
        ox, oy = int(round(M2[0, 2])), int(round(M2[1, 2]))
        sx0, sy0 = max(0, -ox), max(0, -oy)
        dx0, dy0 = max(0, ox), max(0, oy)
        ww = min(w - sx0, (x1 - x0) - dx0)
        hh = min(h - sy0, (y1 - y0) - dy0)
        if ww > 0 and hh > 0:
            _blend(canvas[y0 + dy0:y0 + dy0 + hh, x0 + dx0:x0 + dx0 + ww], src[sy0:sy0 + hh, sx0:sx0 + ww], alpha)
        return
    if not opaque:
        scale = math.hypot(M[0, 0], M[1, 0])
        if scale < 0.5:  # warpAffine has no true AREA filter: pre-shrink so small sprites don't alias
            f = scale * 1.5
            src = cv2.resize(src, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA)
            M2[:, :2] /= f
    warped = cv2.warpAffine(src, M2, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    _blend(canvas[y0:y1, x0:x1], warped, alpha)


@lru_cache(maxsize=2)
def _grain_tiles(amount: float, n: int, seed: int):
    """Film grain as saturating uint8 (add, subtract) tile pairs, built once per process.
    Same noise field as the float version: N(0,1) at half resolution, bilinearly upscaled."""
    rng = np.random.default_rng(seed)
    tiles = []
    for _ in range(n):
        g = rng.normal(0, 1, (H // 2, W // 2)).astype(np.float32)
        g = cv2.resize(g, (W, H), interpolation=cv2.INTER_LINEAR) * (amount * 255.0)
        pos = cv2.convertScaleAbs(np.maximum(g, 0))
        neg = cv2.convertScaleAbs(np.maximum(-g, 0))
        z = np.zeros_like(pos)
        tiles.append((cv2.merge((pos, pos, pos, z)), cv2.merge((neg, neg, neg, z))))
    return tiles


class Grain:
    def __init__(self, amount: float, n=6, seed=99):
        self.amount = amount
        self.tiles = _grain_tiles(float(amount), n, seed) if amount > 0 else []

    def index(self, frame: int) -> int:
        return (frame // 2) % len(self.tiles) if self.tiles else -1

    def apply(self, canvas, frame):
        if self.tiles:
            pos, neg = self.tiles[self.index(frame)]
            cv2.add(canvas, pos, dst=canvas)
            cv2.subtract(canvas, neg, dst=canvas)


def _layer_pose(scene: Scene, layer: Layer, i: int, t: float, sp: Sprite):
    st = layer.anim(t) if layer.anim else State()
    if st.alpha <= 0.001:
        return None
    M = _matrix(layer, st, scene.camera, t, sp, _boil(layer, t, scene.stop_motion_fps))
    rv = layer.reveal(t) if layer.reveal else 1.0
    return st, M, rv


def _fade(scene: Scene, t: float) -> float:
    f = 1.0
    if scene.fade_in and t < scene.fade_in:
        f *= t / scene.fade_in
    if scene.fade_out and t > scene.duration - scene.fade_out:
        f *= max(0.0, (scene.duration - t) / scene.fade_out)
    return f


def render_rgba(scene: Scene, i: int, grain: Grain | None = None) -> np.ndarray:
    """Frame `i` as an RGBA uint8 (H, W, 4) array (the alpha channel is scratch, ignore it)."""
    t = i / FPS
    canvas = None
    for layer in scene.layers:
        sp = layer.frame_fn(i) if layer.frame_fn else layer.sprite
        pose = _layer_pose(scene, layer, i, t, sp)
        if pose is None:
            continue
        st, M, rv = pose
        if canvas is None:
            if sp.px is None:
                # opaque first layer (background / full-frame photo): warp straight into a fresh frame;
                # identical to compositing onto black, without touching the frame twice
                canvas = cv2.warpAffine(sp.pm8(), M, (W, H), flags=cv2.INTER_LINEAR,
                                        borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
                if st.alpha < 0.999:
                    cv2.convertScaleAbs(canvas, dst=canvas, alpha=st.alpha)
                continue
            canvas = np.zeros((H, W, 4), np.uint8)
        draw(canvas, sp, M, st.alpha, rv)
    if canvas is None:
        canvas = np.zeros((H, W, 4), np.uint8)
    if grain:
        grain.apply(canvas, i)
    f = _fade(scene, t)
    if f < 1.0:
        cv2.convertScaleAbs(canvas, dst=canvas, alpha=f)
    return canvas


def render_frame(scene: Scene, i: int, grain: Grain | None = None) -> np.ndarray:
    """Frame `i` as RGB uint8 (H, W, 3)."""
    return cv2.cvtColor(render_rgba(scene, i, grain), cv2.COLOR_RGBA2RGB)


def render_yuv(scene: Scene, i: int, grain: Grain | None = None) -> np.ndarray:
    """Frame `i` as planar yuv420p (BT.601 limited range, same as ffmpeg's default rgb24->yuv420p)."""
    return cv2.cvtColor(render_rgba(scene, i, grain), cv2.COLOR_RGBA2YUV_I420)


def frame_key(scene: Scene, i: int, grain: Grain | None = None):
    """Everything frame `i`'s pixels depend on. Two frames with equal keys are identical, so the
    renderer can reuse the previous frame (held stop-motion poses with a locked camera). None = unique."""
    t = i / FPS
    parts = []
    for k, layer in enumerate(scene.layers):
        if layer.frame_fn:
            return None  # video: every frame differs
        pose = _layer_pose(scene, layer, i, t, layer.sprite)
        if pose is None:
            continue
        st, M, rv = pose
        parts.append((k, tuple(np.round(M.ravel(), 4).tolist()), round(st.alpha, 4), round(rv, 4)))
    return (tuple(parts), grain.index(i) if grain else -1, round(_fade(scene, t), 4))


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
