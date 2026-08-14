"""Job storage: thread-safe in-memory job management."""

from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


class JobStore:
    """Thread-safe in-memory job store with automatic cleanup."""

    MAX_COMPLETED_JOBS = 500
    CLEANUP_THRESHOLD = 600

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()

    def _cleanup_old_jobs(self) -> None:
        completed = [
            (jid, j) for jid, j in self.jobs.items()
            if j.get("status") in {"completed", "resource_mismatch", "failed"}
        ]
        if len(completed) <= self.MAX_COMPLETED_JOBS:
            return
        completed.sort(key=lambda x: x[1].get("updated_at", ""))
        for jid, _ in completed[:len(completed) - self.MAX_COMPLETED_JOBS]:
            del self.jobs[jid]

    def create(self, job_id: str, request: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            if len(self.jobs) >= self.CLEANUP_THRESHOLD:
                self._cleanup_old_jobs()
            self.jobs[job_id] = {
                "job_id": job_id, "status": "queued", "progress": 0,
                "current_step": "queued", "message": "Task created.",
                "pending_choices": [], "error": None, "request": request,
                "result": None, "created_at": now, "updated_at": now,
            }

    def update(self, job_id: str, **values: Any) -> None:
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            self.jobs[job_id].update(values)
            self.jobs[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()

    def get(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            return deepcopy(self.jobs[job_id])
