"""Render an MLT project as fast as the machine allows.

Video: the timeline is cut into slices that `melt` renders in parallel (one process per ~4 cores),
encoded on NVENC when there's an NVIDIA GPU (up to 8 sessions on GeForce), then joined losslessly.
Audio: narration + optional music bed (ducked under the voice) mixed once by ffmpeg, mastered to
YouTube loudness, then muxed without re-encoding the picture.
"""
from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def run(cmd: list[str], cwd=None) -> None:
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({p.returncode}): {p.stdout[-1500:]}")


def encoder() -> tuple[str, list[str], int]:
    """(name, melt consumer properties, max parallel sessions)."""
    probe = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.1",
                            "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
    if probe.returncode == 0:
        return "h264_nvenc", ["vcodec=h264_nvenc", "preset=p5", "tune=hq", "rc=vbr", "cq=21", "vb=0",
                              "g=60", "pix_fmt=yuv420p"], 8
    return "libx264", ["vcodec=libx264", "preset=superfast", "crf=20", "g=60", "pix_fmt=yuv420p"], 64


def render_video(project: Path, total_frames: int, out: Path, workdir: Path, slices: int | None = None) -> str:
    name, props, max_sessions = encoder()
    cores = os.cpu_count() or 4
    slices = slices or max(1, min(max_sessions, cores // 4 or 1, total_frames // 300 or 1))
    per = -(-total_frames // slices)
    threads = max(1, cores // slices)
    workdir.mkdir(parents=True, exist_ok=True)

    def one(i: int) -> Path:
        a, b = i * per, min(total_frames, (i + 1) * per) - 1
        seg = workdir / f"seg{i:03d}.mp4"
        run(["melt", project.name, f"in={a}", f"out={b}", "-consumer", f"avformat:{seg}", "an=1",
             "real_time=-1", f"threads={threads}", *props], cwd=project.parent)
        return seg

    with ThreadPoolExecutor(slices) as ex:
        segs = list(ex.map(one, range(slices)))
    concat(segs, out)
    return f"{name} x{slices}"


def concat(parts: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", "-movflags", "+faststart", str(out)])
    lst.unlink()


def mix_audio(narration: Path, out: Path, music: Path | None = None) -> None:
    if music:
        graph = ("[0:a]aresample=48000,loudnorm=I=-16:TP=-1.5,asplit=2[v][key];"
                 "[1:a]aresample=48000,volume=-14dB[m];"
                 "[m][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[md];"
                 "[v][md]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1[a]")
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(narration), "-stream_loop", "-1", "-i", str(music),
               "-filter_complex", graph, "-map", "[a]"]
    else:
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(narration),
               "-af", "aresample=48000,loudnorm=I=-14:TP=-1"]
    run(cmd + ["-c:a", "aac", "-b:a", "192k", str(out)])


def mux(video: Path, audio: Path, out: Path) -> None:
    run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a",
         "-c", "copy", "-shortest", "-movflags", "+faststart", str(out)])


def duration(path: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(p.stdout.strip() or 0)
