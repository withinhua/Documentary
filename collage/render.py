"""Render one collage scene spec to an H.264 mp4 (1920x1080, 30 fps, yuv420p, no audio).

    python -m collage.render spec.json out.mp4
    python -m collage.render --demo outdir/
    python -m collage.render --still 2.0 spec.json frame.jpg     # single frame, for previews
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from .anim import FPS, Grain, frame_key, render_frame, render_yuv
from .imaging import H, W
from .scenes import build

cv2.setNumThreads(1)  # we parallelise across frames instead

DEFAULT_CACHE = Path(os.environ.get("COLLAGE_CACHE", Path(tempfile.gettempdir()) / "collage-cache"))


def _ctx(workdir):
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="collage-"))
    wd.mkdir(parents=True, exist_ok=True)
    DEFAULT_CACHE.mkdir(parents=True, exist_ok=True)
    return {"workdir": wd, "cache": DEFAULT_CACHE}


# x264 settings. `superfast` at CRF 18 measured SSIM >= `veryfast` at CRF 18 on these grainy collage frames
# for ~55% of the encoder CPU (files ~15% larger); frames are piped as yuv420p (converted with
# cv2, BT.601 limited range like ffmpeg's default) because ffmpeg's rgb24->yuv420p swscale step alone
# cost ~20 ms/frame.
DEFAULT_CRF = 18
DEFAULT_PRESET = "superfast"


def render_scene(spec: dict, out_path, workdir=None, threads: int | None = None, crf: int = DEFAULT_CRF,
                 preset: str = DEFAULT_PRESET, encoder_threads: int | None = None) -> Path:
    """Render ONE scene spec to `out_path` (mp4). Returns the output path.

    `threads`: frame-compositing threads (default: all cores). `encoder_threads`: x264 threads
    (default: x264's own choice). When several scenes render at once (pipeline process pool) pass
    small values for both so the machine isn't oversubscribed.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ctx = _ctx(workdir)
    try:
        return _render(spec, out_path, ctx, threads, crf, preset, encoder_threads)
    finally:
        if workdir is None:
            shutil.rmtree(ctx["workdir"], ignore_errors=True)


def _render(spec, out_path, ctx, threads, crf, preset, encoder_threads=None) -> Path:
    scene = build(spec, ctx)
    n = int(round(float(spec["duration"]) * FPS))
    grain = Grain(scene.grain)
    tmp = out_path.with_name(out_path.stem + ".part" + out_path.suffix)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
           *(["-threads", str(encoder_threads)] if encoder_threads else []),
           "-pix_fmt", "yuv420p", "-r", str(FPS), "-frames:v", str(n), "-movflags", "+faststart", str(tmp)]
    # Frames whose inputs are identical to the previous frame's (held stop-motion poses under a locked
    # camera, grain held on twos) are written again instead of recomputed.
    keys = [frame_key(scene, i, grain) for i in range(n)]
    same = [i > 0 and keys[i] is not None and keys[i] == keys[i - 1] for i in range(n)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    workers = threads or max(1, (os.cpu_count() or 2))
    try:
        with ThreadPoolExecutor(workers) as ex:
            todo = [i for i in range(n) if not same[i]]
            window = workers * 2
            futs = {}
            nxt = 0
            while nxt < len(todo) and len(futs) < window:
                futs[todo[nxt]] = ex.submit(render_yuv, scene, todo[nxt], grain)
                nxt += 1
            last = None
            for i in range(n):
                if not same[i]:
                    last = futs.pop(i).result()
                    if nxt < len(todo):
                        futs[todo[nxt]] = ex.submit(render_yuv, scene, todo[nxt], grain)
                        nxt += 1
                proc.stdin.write(last)
    finally:
        try:
            proc.stdin.close()
        except BrokenPipeError:
            pass
        rc = proc.wait()
    if rc != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed ({rc}) for {out_path}")
    os.replace(tmp, out_path)  # atomic: a half-written clip never looks finished (render cache)
    return out_path


# --------------------------------------------------------------------------------------- render cache
MEDIA_KEYS = ("photo", "video", "src")


def _code_version() -> str:
    """Hash of the renderer's own source, so any change to the look invalidates cached clips."""
    h = hashlib.sha256()
    for f in sorted(Path(__file__).resolve().parent.glob("*.py")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def spec_media(spec) -> list[str]:
    """Every local media file a spec references (photo, video, bg.src, nested), in a stable order."""
    out: list[str] = []

    def walk(v, key=None):
        if isinstance(v, dict):
            for k in sorted(v):
                walk(v[k], k)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif isinstance(v, str) and key in MEDIA_KEYS and v not in out:
            out.append(v)
    walk(spec)
    return out


def cache_key(spec: dict, crf: int = DEFAULT_CRF, preset: str = DEFAULT_PRESET, media_root=None) -> str:
    """Content hash of everything a rendered clip depends on: the spec, each referenced media file's
    size + mtime, the encoder settings and the renderer code. Equal key => the existing clip is valid."""
    h = hashlib.sha256()
    h.update(json.dumps(spec, sort_keys=True, default=str).encode())
    for m in spec_media(spec):
        p = Path(media_root, m) if media_root else Path(m)
        try:
            st = p.stat()
            h.update(f"{m}|{st.st_size}|{st.st_mtime_ns}".encode())
        except OSError:
            h.update(f"{m}|missing".encode())
    h.update(f"{crf}|{preset}|{_code_version()}".encode())
    return h.hexdigest()[:24]


def render_still(spec: dict, t: float, out_path, workdir=None) -> Path:
    ctx = _ctx(workdir)
    scene = build(spec, ctx)
    i = min(int(round(t * FPS)), int(round(float(spec["duration"]) * FPS)) - 1)
    frame = render_frame(scene, i, Grain(scene.grain))
    cv2.imwrite(str(out_path), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return Path(out_path)


# --------------------------------------------------------------------------------------- demo
TEST_PHOTO = Path(os.environ.get(
    "COLLAGE_TEST_PHOTO",
    "/tmp/claude-0/-home-user-Documentary/9d0c190d-a21d-52be-9101-ff4b1389a2da/images/1.webp"))


def demo_specs(photo: Path = TEST_PHOTO) -> dict[str, dict]:
    p = str(photo)
    return {
        "01_title": {"type": "title", "duration": 4, "kicker": "Chapter 2", "title": "The Rear-Engined Revolution"},
        "02_cutout_paper": {"type": "cutout", "duration": 5, "photo": p, "position": "right", "bg": {"type": "paper"},
                            "caption": {"text": "Colin Chapman", "sub": "Founder, Lotus Cars"}, "motion": "push"},
        "03_cutout_photo_bg": {"type": "cutout", "duration": 5, "photo": p, "position": "left",
                               "bg": {"type": "photo", "src": p}, "caption": {"text": "Hethel, 1966"},
                               "motion": "drift"},
        "04_number_card": {"type": "number_card", "duration": 5, "number": "2", "label": "Second Place",
                           "sub": "Colin Chapman", "bg": {"type": "paper"}, "photo": p, "photo_position": "right"},
        "05_number_card_photo_bg": {"type": "number_card", "duration": 4, "number": "1963", "label": "Lotus 29",
                                    "bg": {"type": "photo", "src": p}},
        "06_full_halftone": {"type": "full", "duration": 4, "photo": p, "motion": "push", "style": "halftone",
                             "caption": {"text": "The Lotus works team"}},
        "07_full_archival": {"type": "full", "duration": 4, "photo": p, "motion": "left", "style": "archival"},
        "08_quote": {"type": "quote", "duration": 6, "photo": p,
                     "text": "Simplify, then add lightness.", "attribution": "Colin Chapman"},
        "09_quote_plain": {"type": "quote", "duration": 6,
                           "text": "Adding power makes you faster on the straights. Subtracting weight makes you faster everywhere.",
                           "attribution": "Colin Chapman"},
        "10_headline": {"type": "headline", "duration": 5, "text": "Lotus Stuns Indianapolis",
                        "publication": "The Evening Chronicle", "date": "Tuesday, June 1, 1965", "photo": p},
    }


def run_demo(outdir: Path, stills_dir: Path | None = None, only: list[str] | None = None, photo: Path = TEST_PHOTO):
    if not Path(photo).exists():
        raise SystemExit(f"demo test photo not found: {photo} (pass --photo or set COLLAGE_TEST_PHOTO)")
    outdir.mkdir(parents=True, exist_ok=True)
    specs = demo_specs(Path(photo))
    stills_dir = stills_dir or Path(__file__).resolve().parent / "demo_stills"
    stills_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for name, spec in specs.items():
        if only and not any(o in name for o in only):
            continue
        t = time.time()
        out = render_scene(spec, outdir / f"{name}.mp4", workdir=outdir / ".work")
        dt = time.time() - t
        report.append((name, spec["duration"], dt))
        print(f"{name:28s} {spec['duration']:>4}s  rendered in {dt:5.1f}s", flush=True)
        for frac in (0.12, 0.5, 0.95):
            ts = spec["duration"] * frac
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{ts:.2f}", "-i", str(out), "-frames:v", "1",
                            "-vf", "scale=960:-2", "-q:v", "4", str(stills_dir / f"{name}_{int(frac * 100):02d}.jpg")],
                           check=True)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m collage.render", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?", help="scene spec JSON file (or '-' for stdin)")
    ap.add_argument("out", nargs="?", help="output .mp4 (or .jpg with --still)")
    ap.add_argument("--demo", metavar="OUTDIR", help="render a demo reel of every scene type")
    ap.add_argument("--only", nargs="*", help="with --demo: only scenes whose name contains one of these")
    ap.add_argument("--photo", default=str(TEST_PHOTO), help="with --demo: the test photo to use")
    ap.add_argument("--still", type=float, metavar="SECONDS", help="render a single frame at this time")
    ap.add_argument("--workdir")
    a = ap.parse_args(argv)
    if a.demo:
        run_demo(Path(a.demo), only=a.only, photo=Path(a.photo))
        return 0
    if not a.spec or not a.out:
        ap.error("spec and out are required (or use --demo)")
    spec = json.load(sys.stdin if a.spec == "-" else open(a.spec))
    if a.still is not None:
        render_still(spec, a.still, a.out, a.workdir)
    else:
        t = time.time()
        render_scene(spec, a.out, a.workdir)
        print(f"{a.out}: {time.time() - t:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
