"""Make a small synthetic job (stand-in footage + a photo) to test the worker end to end.

    python examples/make_demo.py projects/demo
"""
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "projects/demo")
(root / "media").mkdir(parents=True, exist_ok=True)


def ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


for name, src in {"archive_a": "mandelbrot=s=1280x720:r=30", "archive_b": "testsrc2=s=1280x720:r=30",
                  "archive_c": "life=s=1280x720:r=30:mold=10:ratio=0.2:death_color=#1f2a44:life_color=#c8b27a"}.items():
    ff("-f", "lavfi", "-i", src, "-t", "25", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
       str(root / "media" / f"{name}.mp4"))
ff("-f", "lavfi", "-i", "gradients=s=2400x1600:c0=#304050:c1=#d9c7a0:n=3", "-frames:v", "1",
   str(root / "media" / "portrait.jpg"))

clip = lambda src, t=0.0, **kw: {"type": "clip", "src": f"media/{src}.mp4", "in": t, **kw}
photo = lambda **kw: {"type": "photo", "src": "media/portrait.jpg", **kw}
demo_src = {"source": "Synthetic test footage (demo)", "license": "CC0", "credit": "Generated locally",
            "url": None}
job = {
    "slug": "demo", "title": "Demo: pipeline test (synthetic footage)",
    "topic": "Why Coca-Cola replaced its formula in 1985, and how the backlash saved the brand",
    "sources": {f"media/{n}": dict(demo_src) for n in ("archive_a.mp4", "archive_b.mp4", "archive_c.mp4", "portrait.jpg")},
    "voice": {"voice": "bm_george", "speed": 0.95},
    "style": {"title": "New Coke", "grain": 6},
    "chapters": [
        {"id": "cold-open", "title": "Cold open", "beats": [
            {"text": "On the morning of April twenty-third, nineteen eighty-five, the most successful product in American history was about to be killed.",
             "visual": clip("archive_a", 2)},
            {"text": "Not by a competitor. By its own makers.", "pace": 0.9, "pause_after": 0.6, "visual": clip("archive_b", 5)},
            {"text": "Inside a hotel ballroom in New York, two hundred reporters waited for an announcement.",
             "visual": photo(motion="push"), "label": {"text": "ROBERTO GOIZUETA", "sub": "CEO, The Coca-Cola Company"}},
        ]},
        {"id": "ch1", "title": "The announcement", "beats": [
            {"text": "Within seventy-nine days, it would become the most famous marketing disaster ever recorded.", "visual": clip("archive_c", 1)},
            {"text": "And yet, by the end of that summer, Coca-Cola would be stronger than it had been in a generation.", "visual": {"hold": True}},
        ]},
        {"id": "ch2", "title": "The secret", "beats": [
            {"text": "To understand how, you have to go back to a secret the company had been hiding for years.",
             "pace": 0.92, "visual": clip("archive_a", 20, motion="left")},
        ]},
    ],
}
(root / "job.json").write_text(json.dumps(job, indent=1))

# The prep stages a real run would do before rendering (research, script, footage), marked done.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.status import Status  # noqa: E402

(root / "status.json").unlink(missing_ok=True)
st = Status(root)
st.stage("research", "done", "Demo: 12 facts from 5 sources (placeholder)")
st.stage("script", "done", "Demo: 3 chapters, 6 beats, cold open + 2 payoffs")
st.stage("footage", "done", "Demo: 3 clips + 1 photo (synthetic, CC0)")
print(root)
