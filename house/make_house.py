"""Generate the house look: one 3D LUT (the grade) and one vignette plate. Same on every video.

    python house/make_house.py [out_dir]

Tweak the numbers here to change the channel's look; every future render picks it up.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

N = 33
SATURATION = 0.82     # < 1 desaturates towards a documentary palette
CONTRAST = 0.16       # strength of the S-curve
WARM_HIGHLIGHTS = 0.03
COOL_SHADOWS = 0.02


def _curve(x: float) -> float:
    return x + CONTRAST * (x - 0.5) * (1 - abs(2 * x - 1))


def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def write_lut(path: Path) -> None:
    lines = ["TITLE \"docburst house\"", f"LUT_3D_SIZE {N}"]
    for bi in range(N):          # .cube order: red changes fastest
        for gi in range(N):
            for ri in range(N):
                r, g, b = ri / (N - 1), gi / (N - 1), bi / (N - 1)
                y = 0.2126 * r + 0.7152 * g + 0.0722 * b
                r, g, b = (y + SATURATION * (c - y) for c in (r, g, b))
                r, g, b = (_clamp(_curve(c)) for c in (r, g, b))
                r += WARM_HIGHLIGHTS * y - 0.5 * COOL_SHADOWS * (1 - y)
                b += -WARM_HIGHLIGHTS * y + COOL_SHADOWS * (1 - y)
                lines.append(f"{_clamp(r):.5f} {_clamp(g):.5f} {_clamp(b):.5f}")
    path.write_text("\n".join(lines) + "\n")


def write_vignette(path: Path, w: int = 1920, h: int = 1080) -> None:
    alpha = f"255*min(1,max(0,(hypot(X-{w/2},Y-{h/2})/{0.57*w:.0f}-0.45)*1.6))"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=black:s={w}x{h},format=rgba,geq=r=0:g=0:b=0:a='{alpha}'",
                    "-frames:v", "1", str(path)], check=True)


def ensure(out_dir) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if not (out / "look.cube").is_file():
        write_lut(out / "look.cube")
    if not (out / "vignette.png").is_file():
        write_vignette(out / "vignette.png")
    return out


if __name__ == "__main__":
    print(ensure(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent))
