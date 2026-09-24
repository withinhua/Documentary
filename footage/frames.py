"""Video keyframes for filmstrips: ffmpeg scene detection on a low-res preview (or a local file).

Only keyframes are decoded (`-skip_frame nokey`) and at most `max_seconds` are read, so a
10-minute 360p preview costs seconds, not minutes. Missing scenes are topped up with evenly
spaced frames so every filmstrip has `n` frames with their timestamps.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit


def as_input(src: str) -> str:
    if src.startswith("file://"):
        return unquote(urlsplit(src).path)
    return src


def probe_duration(src: str, timeout: int = 60) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                              as_input(src)], capture_output=True, text=True, timeout=timeout).stdout
        d = (json.loads(out or "{}").get("format") or {}).get("duration")
        return float(d) if d else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _grab(src: str, t: float, dest: Path, width: int, timeout: int = 60) -> bool:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", as_input(src),
                        "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(dest)],
                       capture_output=True, timeout=timeout)
    return r.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def keyframes(src: str, out_dir: Path, n: int = 4, duration: float | None = None, width: int = 640,
              max_seconds: float = 900, threshold: float = 0.3, timeout: int = 180) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    done = sorted(out_dir.glob("kf_*.jpg"))
    if len(done) >= n:                                   # cached from a previous run
        return [{"t": float(re.search(r"kf_([\d.]+)\.jpg", p.name).group(1)), "path": str(p)} for p in done[:n]]
    duration = duration or probe_duration(src)
    scenes: list[float] = []
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-skip_frame", "nokey", "-t", str(max_seconds), "-i", as_input(src),
             "-vf", f"select='gt(scene,{threshold})',showinfo", "-fps_mode", "vfr", "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout)
        scenes = [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]
    except subprocess.TimeoutExpired:
        scenes = []
    span = min(duration or max_seconds, max_seconds)
    times: list[float] = []
    if len(scenes) >= n:                                 # spread picks across the film
        for i in range(n):
            target = (i + 0.5) * span / n
            times.append(min(scenes, key=lambda s: abs(s - target)))
        times = sorted(set(times))
    else:
        times = sorted(set(round(s + 0.5, 2) for s in scenes))
    if duration:                                         # top up with evenly spaced frames
        for m in (n, 2 * n):
            for k in range(m):
                t = round((k + 0.5) * span / m, 2)
                if len(times) < n and all(abs(t - x) > span / (3 * n) for x in times):
                    times.append(t)
    times = sorted(times)[:n] or [0.0]
    frames = []
    for t in times:
        dest = out_dir / f"kf_{t:08.2f}.jpg"
        if _grab(src, t, dest, width):
            frames.append({"t": t, "path": str(dest)})
    return frames
