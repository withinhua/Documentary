"""Narration: one take per beat (sentence), joined with scripted pauses, exact timings for the edit.

Kokoro-82M (Apache-2.0) through ONNX Runtime: on CUDA when present, otherwise CPU.
Measured 2.4-2.7x real time on 4 CPU cores; a GPU makes it far faster.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import soundfile as sf

MODEL_DIR = Path(os.environ.get("KOKORO_DIR", "/opt/models/kokoro"))


class Narrator:
    def __init__(self, voice: str = "bm_george", speed: float = 0.95, model_dir: Path = MODEL_DIR):
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider")
                     if p in ort.get_available_providers()]
        sess = ort.InferenceSession(str(model_dir / "kokoro-v1.0.onnx"), providers=providers)
        self.device = "cuda" if sess.get_providers()[0].startswith("CUDA") else "cpu"
        self.k = Kokoro.from_session(sess, str(model_dir / "voices-v1.0.bin"))
        self.voice, self.speed = voice, speed

    def say(self, text: str, pace: float = 1.0):
        audio, sr = self.k.create(text, voice=self.voice, speed=self.speed * pace, lang="en-us")
        return audio.astype(np.float32), sr

    def narrate(self, chapters: list[dict], out_wav: Path) -> list[list[tuple[float, float]]]:
        """Write the shard's narration; return [(start, end)] seconds per beat, per chapter.
        A beat's slot runs until the next beat starts, so pauses stay on the same picture."""
        parts, times, t, sr = [], [], 0.0, 24000
        for ci, ch in enumerate(chapters):
            ch_times = []
            beats = ch.get("beats", [])
            for bi, b in enumerate(beats):
                audio, sr = self.say(b["text"], float(b.get("pace", 1.0)))
                last = bi == len(beats) - 1
                pause = float(b.get("pause_after", 1.2 if last else 0.3))
                gap = np.zeros(int(pause * sr), dtype=np.float32)
                parts += [audio, gap]
                dur = len(audio) / sr + pause
                ch_times.append((t, t + dur))
                t += dur
            times.append(ch_times)
        sf.write(out_wav, np.concatenate(parts) if parts else np.zeros(sr, np.float32), sr)
        return times
