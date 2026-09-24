"""Footage finder: script beat → free/open footage that actually shows the subject.

    python -m footage.find requests.json --out work/footage          # search, filter, contact sheets
    # Claude looks at work/footage/contact/*.jpg (protocol: footage/REVIEW.md) and writes picks.json
    python -m footage.fetch picks.json --candidates work/footage/candidates.json --out work/footage/media

Stages: source adapters (footage/sources/) → licence policy → perceptual-hash dedupe → quality
filters (resolution, blur, text/watermark) → optional CLIP relevance → rank → thumbnails and
video filmstrips → contact sheets → candidates.json. The final judge is Claude's own vision.
"""
