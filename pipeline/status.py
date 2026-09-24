"""Production status: which pipeline stage is running, what it did, what's next.

Every stage calls `Status(project_dir).stage("voice", "running", "Narrating 3 chapters")` and the
Studio UI shows it live (the feed re-publishes status.json on every update).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

STAGES = [
    ("research", "Research"),
    ("script", "Script"),
    ("voice", "Voice"),
    ("footage", "Footage"),
    ("edit", "Edit"),
    ("render", "Render"),
    ("publish", "Publish"),
]


class Status:
    def __init__(self, project_dir, on_change=None):
        self.path = Path(project_dir) / "status.json"
        self.on_change = on_change
        if self.path.is_file():
            self.data = json.loads(self.path.read_text())
        else:
            self.data = {"stages": [{"id": i, "label": l, "status": "pending"} for i, l in STAGES],
                         "activity": [], "createdAt": time.time()}

    def _save(self) -> None:
        self.data["updatedAt"] = time.time()
        self.path.write_text(json.dumps(self.data, indent=1))
        if self.on_change:
            self.on_change()

    def stage(self, stage_id: str, status: str, detail: str | None = None, **extra) -> None:
        now = time.time()
        for s in self.data["stages"]:
            if s["id"] == stage_id:
                if status == "running" and s.get("status") != "running":
                    s["startedAt"] = now
                if status in ("done", "failed", "skipped"):
                    s["endedAt"] = now
                s["status"] = status
                if detail:
                    s["detail"] = detail
                s.update(extra)
        if detail:
            self.log(stage_id, detail, save=False)
        self._save()

    def reset(self, stage_ids: list[str]) -> None:
        """A new run of these stages is starting: clear their old results so the Studio doesn't show
        a previous run's 'done' next to this run's progress."""
        for s in self.data["stages"]:
            if s["id"] in stage_ids:
                for k in ("startedAt", "endedAt", "detail"):
                    s.pop(k, None)
                s["status"] = "pending"
        self._save()

    def log(self, stage_id: str, message: str, save: bool = True) -> None:
        self.data["activity"].append({"t": time.time(), "stage": stage_id, "message": message})
        self.data["activity"] = self.data["activity"][-500:]
        if save:
            self._save()
