"""Live progress for any pipeline step: update the production's status and republish the Studio feed.

    live = tracker_for(Path("projects/new-coke/footage"))   # any path inside a production
    if live: live.stage("footage", "running", "Searching 51 beats")

The feed folder defaults to `editor/apps/web/public/studio-feed` (what the local Studio serves) or
`$STUDIO_FEED`. Publishing is throttled so tight loops don't spend their time re-publishing.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .status import Status

DEFAULT_FEED = Path("editor/apps/web/public/studio-feed")


def project_dir_of(path: Path) -> Path | None:
    for p in [path.resolve(), *path.resolve().parents]:
        if (p / "script.json").is_file() or (p / "job.json").is_file():
            return p
    return None


def feed_dir() -> Path | None:
    env = os.environ.get("STUDIO_FEED")
    if env:
        return Path(env)
    return DEFAULT_FEED if DEFAULT_FEED.parent.is_dir() else None


class Live(Status):
    def __init__(self, project: Path, feed: Path | None, min_interval: float = 2.0):
        self._project, self._feed = project, feed
        self._last, self._min = 0.0, min_interval
        super().__init__(project, on_change=self._publish)

    def _publish(self, force: bool = False) -> None:
        if not self._feed or (not force and time.time() - self._last < self._min):
            return
        self._last = time.time()
        try:
            from .feed import publish
            renders = Path("out") / self._project.name
            publish(self._project, self._feed, renders if renders.is_dir() else None)
        except Exception as e:  # noqa: BLE001 - progress reporting must never break the real work
            print(f"[live] publish failed: {e}")

    def stage(self, stage_id: str, status: str, detail: str | None = None, **extra) -> None:
        super().stage(stage_id, status, detail, **extra)
        if status in ("done", "failed"):
            self._publish(force=True)


def tracker_for(path: Path) -> Live | None:
    project = project_dir_of(Path(path))
    return Live(project, feed_dir()) if project else None
