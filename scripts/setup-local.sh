#!/usr/bin/env bash
# One-time setup to run the Studio + pipeline on your own computer (macOS or Linux).
set -euo pipefail
cd "$(dirname "$0")/.."

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1 — $2"; exit 1; }; }
need python3 "install Python 3.10+ (python.org or: brew install python)"
need ffmpeg  "brew install ffmpeg   (Linux: sudo apt install ffmpeg)"
need node    "install Node 20+ (nodejs.org or: brew install node)"

echo "→ Python environment (.venv)"
python3 -m venv .venv
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements-local.txt

echo "→ Voice model (Kokoro, ~350 MB, once)"
mkdir -p models/kokoro
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  [ -f "models/kokoro/$f" ] || curl -fL --progress-bar -o "models/kokoro/$f" \
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f"
done

echo "→ House look (LUT + vignette)"
python house/make_house.py >/dev/null
# Show every production in the Studio right away (script only until it's rendered)
for d in projects/*/; do [ -f "$d/script.json" ] && python -m pipeline.feed publish "$d" >/dev/null; done

echo "→ Studio (editor) dependencies"
corepack enable >/dev/null 2>&1 || npm i -g pnpm
(cd editor && pnpm install && pnpm build:wasm)

echo
echo "Done. Next:"
echo "  Terminal 1:  cd editor && pnpm dev          → open http://localhost:5173"
echo "  Terminal 2:  ./scripts/new-coke.sh           → watch it build live in the Studio"
