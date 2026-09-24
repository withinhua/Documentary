"""Render one collage scene spec to an H.264 mp4 (1920x1080, 30 fps, yuv420p, no audio).

    python -m collage.render spec.json out.mp4
    python -m collage.render --demo outdir/
    python -m collage.render --still 2.0 spec.json frame.jpg     # single frame, for previews
"""
from __future__ import annotations

import argparse
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

from .anim import FPS, Grain, render_frame
from .imaging import H, W
from .scenes import build

cv2.setNumThreads(1)  # we parallelise across frames instead

DEFAULT_CACHE = Path(os.environ.get("COLLAGE_CACHE", Path(tempfile.gettempdir()) / "collage-cache"))


def _ctx(workdir):
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="collage-"))
    wd.mkdir(parents=True, exist_ok=True)
    DEFAULT_CACHE.mkdir(parents=True, exist_ok=True)
    return {"workdir": wd, "cache": DEFAULT_CACHE}


def render_scene(spec: dict, out_path, workdir=None, threads: int | None = None, crf: int = 18,
                 preset: str = "veryfast") -> Path:
    """Render ONE scene spec to `out_path` (mp4). Returns the output path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ctx = _ctx(workdir)
    try:
        return _render(spec, out_path, ctx, threads, crf, preset)
    finally:
        if workdir is None:
            shutil.rmtree(ctx["workdir"], ignore_errors=True)


def _render(spec, out_path, ctx, threads, crf, preset) -> Path:
    scene = build(spec, ctx)
    n = int(round(float(spec["duration"]) * FPS))
    grain = Grain(scene.grain)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-r", str(FPS), "-frames:v", str(n), "-movflags", "+faststart", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    workers = threads or max(1, (os.cpu_count() or 2))
    try:
        with ThreadPoolExecutor(workers) as ex:
            window = workers * 2
            futs = {}
            for i in range(min(window, n)):
                futs[i] = ex.submit(render_frame, scene, i, grain)
            for i in range(n):
                frame = futs.pop(i).result()
                j = i + window
                if j < n:
                    futs[j] = ex.submit(render_frame, scene, j, grain)
                proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed ({rc}) for {out_path}")
    return out_path


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
