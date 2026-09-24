"""Narration: one take per beat (sentence), joined with scripted pauses, exact timings for the edit.

Kokoro-82M (Apache-2.0) through ONNX Runtime: on CUDA when present, otherwise CPU.
Measured 2.4-2.7x real time on 4 CPU cores with one session; a GPU makes it far faster.

On CPU a single ONNX session scales poorly over cores for sentence-sized inputs, so `narrate` can
spread the beats over several processes (each with its own session and a share of the cores).
Takes are independent, so the audio is the same as a sequential run; beats come back in script
order as soon as they (and every beat before them) are done, which lets the edit start rendering
the first scenes while later beats are still being voiced (`iter_takes`).
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
import soundfile as sf

MODEL_DIR = Path(os.environ.get("KOKORO_DIR", "/opt/models/kokoro"))
SR = 24000


def _providers():
    import onnxruntime as ort
    return [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]


class Narrator:
    def __init__(self, voice: str = "bm_george", speed: float = 0.95, model_dir: Path = MODEL_DIR,
                 threads: int | None = None, workers: int | None = None):
        """`threads`: ONNX intra-op threads for this process's session (default: ONNX's choice).
        `workers`: processes used by `narrate`/`iter_takes` on CPU (default: auto, see `_auto_workers`)."""
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
            opts.inter_op_num_threads = 1
        sess = ort.InferenceSession(str(model_dir / "kokoro-v1.0.onnx"), opts, providers=_providers())
        self.device = "cuda" if sess.get_providers()[0].startswith("CUDA") else "cpu"
        self.k = Kokoro.from_session(sess, str(model_dir / "voices-v1.0.bin"))
        self.voice, self.speed, self.model_dir, self.workers = voice, speed, Path(model_dir), workers

    def say(self, text: str, pace: float = 1.0):
        audio, sr = self.k.create(text, voice=self.voice, speed=self.speed * pace, lang="en-us")
        return audio.astype(np.float32), sr

    # ── parallel takes ─────────────────────────────────────────────────────────────────────────
    def _auto_workers(self, n_beats: int) -> int:
        if self.workers is not None:
            return max(1, self.workers)
        if self.device == "cuda" or n_beats < 4:
            return 1
        return max(1, min(4, (os.cpu_count() or 2) // 2, n_beats // 2))

    def iter_takes(self, beats: list[dict]) -> Iterator[tuple[int, np.ndarray, int]]:
        """Yield (index, audio, sample_rate) for every beat, in order, as soon as each is ready."""
        n = self._auto_workers(len(beats))
        jobs = [(b["text"], float(b.get("pace", 1.0))) for b in beats]
        if n <= 1:
            for i, (text, pace) in enumerate(jobs):
                audio, sr = self.say(text, pace)
                yield i, audio, sr
            return
        threads = max(1, (os.cpu_count() or 2) // n)
        import multiprocessing as mp
        # spawn, not fork: this process already runs ONNX Runtime threads, which don't survive a fork
        with ProcessPoolExecutor(n, mp_context=mp.get_context("spawn"), initializer=_init_worker,
                                 initargs=(self.voice, self.speed, str(self.model_dir), threads)) as ex:
            # submit everything; results are consumed in order (map keeps order, runs ahead in parallel)
            for i, (audio, sr) in enumerate(ex.map(_take, jobs)):
                yield i, audio, sr

    def narrate(self, chapters: list[dict], out_wav: Path,
                on_beat: Callable[[int, int, float, float], None] | None = None) -> list[list[tuple[float, float]]]:
        """Write the narration; return [(start, end)] seconds per beat, per chapter.
        A beat's slot runs until the next beat starts, so pauses stay on the same picture.
        `on_beat(chapter_index, beat_index, start, end)` fires as soon as a beat's slot is known
        (in order), so callers can start work on it while the rest is still being voiced."""
        flat = [(ci, bi, b) for ci, ch in enumerate(chapters) for bi, b in enumerate(ch.get("beats", []))]
        times: list[list[tuple[float, float]]] = [[] for _ in chapters]
        parts, t, sr = [], 0.0, SR
        for i, audio, sr in self.iter_takes([b for _, _, b in flat]):
            ci, bi, b = flat[i]
            last = bi == len(chapters[ci]["beats"]) - 1
            pause = float(b.get("pause_after", 1.2 if last else 0.3))
            gap = np.zeros(int(pause * sr), dtype=np.float32)
            parts += [audio, gap]
            dur = len(audio) / sr + pause
            times[ci].append((t, t + dur))
            if on_beat:
                on_beat(ci, bi, t, t + dur)
            t += dur
        sf.write(out_wav, np.concatenate(parts) if parts else np.zeros(sr, np.float32), sr)
        return times


# ── process-pool plumbing (one Narrator per worker process) ────────────────────────────────────
_W: Narrator | None = None


def _init_worker(voice: str, speed: float, model_dir: str, threads: int) -> None:
    global _W
    _W = Narrator(voice, speed, Path(model_dir), threads=threads, workers=1)


def _take(job: tuple[str, float]):
    text, pace = job
    return _W.say(text, pace)
