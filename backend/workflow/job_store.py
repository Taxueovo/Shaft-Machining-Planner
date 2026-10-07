# 以线程锁和 SQLite 保存任务状态，使用深拷贝隔离调用方对内部记录的修改。
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


# 维护内存任务映射及 SQLite 副本，以线程锁串行化持久化修改。
class JobStore:
    """Small persistent store; payloads never leave the local machine."""

    MAX_COMPLETED_JOBS = 500
    CLEANUP_THRESHOLD = 600

    def __init__(self, db_path: str | None = None) -> None:
        """打开 SQLite（含 WAL/权限配置）并把既有任务从磁盘载入内存缓存。"""
        configured = (
            db_path if db_path is not None else os.getenv("JOB_DB_FILE", "data/jobs.sqlite3")
        )
        self.db_path = (
            configured if configured == ":memory:" else str(Path(configured).expanduser().resolve())
        )
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        # WAL 模式 + NORMAL 同步：支撑跨线程并发访问，同时尽量保证进程退出后可恢复。
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self.connection.commit()
        self._secure_files()
        self.jobs: dict[str, dict[str, Any]] = {}
        # 启动时一次性载入内存 dict 作为统一读写入口；个别损坏行直接跳过。
        for job_id, payload in self.connection.execute("SELECT job_id, payload FROM jobs"):
            try:
                value = json.loads(payload)
                if isinstance(value, dict):
                    self.jobs[job_id] = value
            except (TypeError, json.JSONDecodeError):
                continue

    def _persist(self, job_id: str) -> None:
        """把单条任务以紧凑 JSON 落盘（UPSERT），写入后同步收紧文件权限。"""
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
        """把 SQLite 主库及 -wal/-shm 伴生文件的权限收紧为 0600（仅当前用户可读写）。"""
        if self.db_path == ":memory:":
            return
        for path in (self.db_path, f"{self.db_path}-wal", f"{self.db_path}-shm"):
            try:
                os.chmod(path, 0o600)
            except FileNotFoundError:
                continue

    def _cleanup_old_jobs(self) -> None:
        """已完成任务数超上限时按更新时间淘汰最旧的超量部分（内存与磁盘同步删除）。"""
        completed = [
            (jid, job)
            for jid, job in self.jobs.items()
            if job.get("status")
            in {"completed", "resource_mismatch", "failed", "interrupted", "cancelled"}
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
        """新建任务并初始化为 queued 状态；缓存接近上限时先触发一次旧任务清理。"""
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
        """原地更新任务字段并刷新 updated_at 后落盘；任务不存在时抛 KeyError。"""
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            if self.jobs[job_id].get("cancel_requested") and values.get("status") not in {
                None,
                "cancelled",
                "cancelling",
            }:
                values["status"] = self.jobs[job_id]["status"]
            self.jobs[job_id].update(values)
            self.jobs[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._persist(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        """返回任务快照的深拷贝，防止外部改动污染内存缓存。"""
        with self.lock:
            if job_id not in self.jobs:
                raise KeyError(job_id)
            return deepcopy(self.jobs[job_id])

    def save_trace(self, job_id: str, entry: dict[str, Any]) -> None:
        """Persist each attempt independently of graph success and result writes."""
        with self.lock:
            if job_id not in self.jobs:
                return
            entries = self.jobs[job_id].setdefault("execution_trace", [])
            for index, previous in enumerate(entries):
                if previous.get("trace_id") == entry["trace_id"]:
                    entries[index] = deepcopy(entry)
                    break
            else:
                entries.append(deepcopy(entry))
            self.jobs[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._persist(job_id)

    def stats(self) -> dict[str, int]:
        """统计内存缓存中的活跃（运行/排队/等待用户）任务数与任务总数。"""
        with self.lock:
            active = sum(
                job.get("status")
                in {
                    "running",
                    "queued",
                    "cancelling",
                    "waiting_user_choice",
                    "waiting_engineering_input",
                }
                or job.get("harness", {}).get("active", False)
                for job in self.jobs.values()
            )
            return {"active_jobs": active, "total_jobs": len(self.jobs)}

    def interrupt_inflight(self) -> int:
        """At single-process startup, expose abandoned jobs instead of fake progress."""
        with self.lock:
            ids = [
                jid
                for jid, job in self.jobs.items()
                if job["status"] in {"queued", "running", "cancelling"}
            ]
            for jid in ids:
                old = self.jobs[jid]
                harness = {**old.get("harness", {}), "active": False, "phase": "interrupted"}
                self.update(
                    jid,
                    status="cancelled" if old.get("cancel_requested") else "interrupted",
                    harness=harness,
                    current_step="interrupted",
                    message="服务已重启，任务执行中断。可从已保存的输入重新创建规划；旧任务记录保留。",
                )
            for jid, job in self.jobs.items():
                if jid not in ids and job.get("harness", {}).get("active"):
                    record = {**job["harness"], "active": False, "phase": "interrupted"}
                    self.update(jid, harness=record)
            return len(ids)

    def list_jobs(self, *, status=None, search="", limit=20, offset=0) -> dict:
        """Paginated summaries; never return drawing bodies, traces or exception text."""
        with self.lock:
            counts = {}
            matches = []
            needle = search.strip().casefold()
            for jid, job in self.jobs.items():
                counts[job["status"]] = counts.get(job["status"], 0) + 1
                req = job.get("request", {})
                title = req.get("part_name") or f"{req.get('material', '—')} · 轴件规划"
                statuses = {
                    "active": {"queued", "running", "cancelling"},
                    "waiting": {"waiting_user_choice", "waiting_engineering_input"},
                }
                if status and job["status"] not in statuses.get(status, {status}):
                    continue
                if needle and needle not in f"{jid} {title} {req.get('material', '')}".casefold():
                    continue
                matches.append(
                    {
                        "job_id": jid,
                        "title": title,
                        "material": req.get("material"),
                        "blank_diameter_mm": req.get("blank_diameter_mm"),
                        "total_length_mm": sum(
                            s.get("length_mm", 0) for s in req.get("segments", [])
                        ),
                        "status": job["status"],
                        "progress": job.get("progress", 0),
                        "created_at": job["created_at"],
                        "updated_at": job["updated_at"],
                        "route_revision": job.get("route_revision", 0),
                    }
                )
            matches.sort(key=lambda j: (j["created_at"], j["job_id"]), reverse=True)
            return {
                "items": matches[offset : offset + limit],
                "total": len(matches),
                "limit": limit,
                "offset": offset,
                "counts": counts,
            }
