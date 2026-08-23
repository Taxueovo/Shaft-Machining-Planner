"""Thread-safe SQLite-backed job storage for restart-safe local planning."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JobStore:
    """Small persistent store; payloads never leave the local machine."""

    MAX_COMPLETED_JOBS = 500
    CLEANUP_THRESHOLD = 600

    def __init__(self) -> None:
        configured = os.getenv("JOB_DB_FILE", "data/jobs.sqlite3")
        self.db_path = (
            configured if configured == ":memory:" else str(Path(configured).expanduser().resolve())
        )
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self.connection.commit()
        self._secure_files()
        self.jobs: dict[str, dict[str, Any]] = {}
        for job_id, payload in self.connection.execute("SELECT job_id, payload FROM jobs"):
            try:
                value = json.loads(payload)
                if isinstance(value, dict):
                    self.jobs[job_id] = value
            except (TypeError, json.JSONDecodeError):
                continue

    def _persist(self, job_id: str) -> None:
        payload = json.dumps(
            self.jobs[job_id], ensure_ascii=False, default=str, separators=(",", ":")
        )
        self.connection.execute(
            "INSERT INTO jobs (job_id, payload, updated_at) VALUES (?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
            (job_id, payload, self.jobs[job_id]["updated_at"]),
        )
        self.connection.commit()
        self._secure_files()

    def _secure_files(self) -> None:
        if self.db_path == ":memory:":
            return
        for path in (self.db_path, f"{self.db_path}-wal", f"{self.db_path}-shm"):
            try:
                os.chmod(path, 0o600)
            except FileNotFoundError:
                continue

    def _cleanup_old_jobs(self) -> None:
        completed = [
            (jid, job)
            for jid, job in self.jobs.items()
            if job.get("status") in {"completed", "resource_mismatch", "failed"}
        ]
        if len(completed) <= self.MAX_COMPLETED_JOBS:
            return
        completed.sort(key=lambda item: item[1].get("updated_at", ""))
        ids = [jid for jid, _ in completed[: len(completed) - self.MAX_COMPLETED_JOBS]]
        for job_id in ids:
            del self.jobs[job_id]
        self.connection.executemany(
            "DELETE FROM jobs WHERE job_id = ?", [(job_id,) for job_id in ids]
        )
        self.connection.commit()

    def create(self, job_id: str, request: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            if len(self.jobs) >= self.CLEANUP_THRESHOLD:
                self._cleanup_old_jobs()
            self.jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "progress": 0,
                "current_step": "queued",
                "message": "Task created.",
                "pending_choices": [],
                "error": None,
                "request": request,
                "result": None,
                "created_at": now,
                "updated_at": now,
            }
            self._persist(job_id)

    def update(self, job_id: str, **values: Any) -> None:
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            self.jobs[job_id].update(values)
            self.jobs[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._persist(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            return deepcopy(self.jobs[job_id])

    def stats(self) -> dict[str, int]:
        with self.lock:
            active = sum(
                job.get("status") in {"running", "queued", "waiting_user_choice"}
                for job in self.jobs.values()
            )
            return {"active_jobs": active, "total_jobs": len(self.jobs)}
