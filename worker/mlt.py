"""Build the shard's edit as an MLT project: the format Shotcut opens and `melt` renders headlessly.

Tracks: V1 footage and photos (each beat is its own clip carrying the house grade, grain, motion and
labels), V2 the house vignette plate, A1 narration. Every path is relative to the job folder, so
the whole folder can be opened in Shotcut on any machine.
"""
from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

W, H, FPS = 1920, 1080, 30

# Ken Burns presets as (start rect, end rect) in x y w h; stills always move, clips optionally drift.
MOTION = {
    "push":  ((0, 0, W, H), (-W * .06, -H * .06, W * 1.12, H * 1.12)),
    "pull":  ((-W * .08, -H * .08, W * 1.16, H * 1.16), (0, 0, W, H)),
    "left":  ((0, -H * .04, W * 1.08, H * 1.08), (-W * .08, -H * .04, W * 1.08, H * 1.08)),
    "right": ((-W * .08, -H * .04, W * 1.08, H * 1.08), (0, -H * .04, W * 1.08, H * 1.08)),
}
PHOTO_EXT = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff")


def _prop(name: str, value) -> str:
    return f'<property name="{name}">{escape(str(value))}</property>'


def _filter(service: str, props: dict, span: tuple[int, int] | None = None) -> str:
    attrs = f' in="{span[0]}" out="{span[1]}"' if span else ""
    return f'<filter mlt_service="{service}"{attrs}>' + "".join(_prop(k, v) for k, v in props.items()) + "</filter>"


def _rect(r) -> str:
    return " ".join(f"{v:.0f}" for v in r) + " 1"


def _beats(chapters, times):
    """Flatten to (beat, start_s, end_s), merging `hold` beats into the previous picture."""
    out = []
    for ch, ch_times in zip(chapters, times):
        for b, (s, e) in zip(ch.get("beats", []), ch_times):
            v = b.get("visual") or {}
            if out and (not v or v.get("hold")):
                prev = out[-1]
                out[-1] = (prev[0], prev[1], e, prev[3] + ([b["label"]] if b.get("label") else []))
                continue
            out.append((b, s, e, [b["label"]] if b.get("label") else []))
    return out


def build(chapters: list[dict], times: list[list[tuple[float, float]]], style: dict | None = None) -> tuple[str, int]:
    style = style or {}
    font = style.get("font", "DejaVu Serif")
    accent = style.get("label_colour", "0xf5e6c8ff")
    producers, entries, frame = [], [], 0
    for i, (beat, s, e, labels) in enumerate(_beats(chapters, times)):
        start_f, end_f = round(s * FPS), round(e * FPS)
        n = max(1, end_f - max(start_f, frame))
        frame += n
        v = beat.get("visual") or {}
        src = v.get("src")
        is_photo = bool(src) and src.lower().endswith(PHOTO_EXT)
        if not src:
            src, is_photo = "color:black", False
        first = 0 if is_photo or src.startswith("color:") else round(float(v.get("in", 0)) * FPS)
        last = first + n - 1
        span = (first, last)
        filters = [
            _filter("avfilter.lut3d", {"av.file": "house/look.cube", "av.interp": "nearest"}),
            _filter("avfilter.noise", {"av.alls": style.get("grain", 6), "av.allf": "u"}),
        ]
        motion = v.get("motion") or ("push" if is_photo else None)
        if motion in MOTION:
            a, b = MOTION[motion]
            filters.append(_filter("affine", {"transition.rect": f"0={_rect(a)};{n - 1}={_rect(b)}",
                                              "transition.fill": 1, "transition.distort": 0,
                                              "background": "color:black"}, span))
        for lab in labels[:1]:
            text = lab["text"] + (f"\n{lab['sub']}" if lab.get("sub") else "")
            filters.append(_filter("dynamictext", {
                "argument": text.replace("#", "\\#"), "geometry": "110 840 1500 170",
                "halign": "left", "valign": "bottom", "family": font, "size": 50, "weight": 600,
                "fgcolour": accent, "bgcolour": "0x00000000", "olcolour": "0x000000aa", "outline": 2,
                "pad": 14}, (first, min(last, first + FPS * 4))))
        extra = _prop("length", 100000) if is_photo or src.startswith("color:") else ""
        producers.append(f'<producer id="b{i}" in="{first}" out="{last}">{_prop("resource", src)}{extra}'
                         + "".join(filters) + "</producer>")
        entries.append(f'<entry producer="b{i}" in="{first}" out="{last}"/>')
    total = frame
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<mlt LC_NUMERIC="C" producer="main" root=".">
<profile description="HD 1080p 30 fps" width="{W}" height="{H}" progressive="1" sample_aspect_num="1" sample_aspect_den="1" display_aspect_num="16" display_aspect_den="9" frame_rate_num="{FPS}" frame_rate_den="1" colorspace="709"/>
{chr(10).join(producers)}
<playlist id="V1">{''.join(entries)}</playlist>
<producer id="vignette" in="0" out="{total - 1}">{_prop("resource", "house/vignette.png")}{_prop("length", total)}</producer>
<playlist id="V2"><entry producer="vignette" in="0" out="{total - 1}"/></playlist>
<producer id="narration" in="0" out="{total - 1}">{_prop("resource", "narration.wav")}</producer>
<playlist id="A1"><entry producer="narration" in="0" out="{total - 1}"/></playlist>
<tractor id="main" in="0" out="{total - 1}" title={quoteattr(style.get("title", "docburst"))}>
<multitrack><track producer="V1"/><track producer="V2"/><track producer="A1" hide="video"/></multitrack>
<transition mlt_service="composite">{_prop("a_track", 0)}{_prop("b_track", 1)}{_prop("always_active", 1)}</transition>
<transition mlt_service="mix">{_prop("a_track", 0)}{_prop("b_track", 2)}{_prop("always_active", 1)}{_prop("sum", 1)}</transition>
</tractor>
</mlt>
"""
    return xml, total
