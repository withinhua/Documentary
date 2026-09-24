#!/usr/bin/env bash
# Rebuild the New Coke documentary locally and publish every stage to the local Studio.
set -euo pipefail
cd "$(dirname "$0")/.."
. .venv/bin/activate
export KOKORO_DIR="$PWD/models/kokoro"
FEED=editor/apps/web/public/studio-feed
P=projects/new-coke

echo "→ Downloading the approved footage (originals, incl. fair-use clips)"
python -m footage.fetch $P/footage/picks.json --candidates $P/footage/candidates.json --out $P/footage/media || true

echo "→ Producing (narration → collage scenes → final cut); the Studio updates as it goes"
python -m pipeline.produce $P --media $P/footage/media --out out/new-coke --feed $FEED
echo "Done: out/new-coke/final.mp4  (also in the Studio at http://localhost:5173)"
