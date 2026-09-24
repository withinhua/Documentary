"""Asset locations: fonts (committed, OFL/Apache) and ONNX models (downloaded on first use)."""
from __future__ import annotations

import os
import shutil
import urllib.request
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
FONTS = ASSETS / "fonts"
MODELS = Path(os.environ.get("COLLAGE_MODELS", ASSETS / "models"))

FONT = {
    "typewriter": FONTS / "SpecialElite-Regular.ttf",
    "numeral": FONTS / "ArchivoBlack-Regular.ttf",
    "condensed": FONTS / "Anton-Regular.ttf",
    "serif": FONTS / "OldStandard-Regular.ttf",
    "serif_bold": FONTS / "OldStandard-Bold.ttf",
    "serif_italic": FONTS / "OldStandard-Italic.ttf",
    "display_serif": FONTS / "PlayfairDisplay.ttf",
    "blackletter": FONTS / "UnifrakturMaguntia-Book.ttf",
}

MODEL_URLS = {
    "isnet-general-use.onnx": "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx",
    "u2netp.onnx": "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx",
}


def model_path(name: str = "isnet-general-use.onnx") -> Path:
    """Return a local path to the named ONNX model, downloading it on first use."""
    MODELS.mkdir(parents=True, exist_ok=True)
    p = MODELS / name
    if p.exists() and p.stat().st_size > 1_000_000:
        return p
    tmp = p.with_suffix(".part")
    with urllib.request.urlopen(MODEL_URLS[name], timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    tmp.rename(p)
    return p
