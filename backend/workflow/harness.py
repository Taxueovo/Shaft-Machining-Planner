"""Persistent execution policy around LangGraph; no manufacturing logic lives here."""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

_control = ContextVar("workflow_execution_control", default=None)
HARNESS_VERSION = "1"


class HarnessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_nodes: int = Field(default=128, ge=1, le=512)
    max_model_calls: int = Field(default=32, ge=1, le=128)
    max_tool_calls: int = Field(default=256, ge=1, le=1024)
    max_active_seconds: float = Field(default=300, gt=0, le=3600)
    model_timeout_seconds: float = Field(default=30, gt=0, le=120)
    model_output_tokens: int = Field(default=4096, ge=128, le=16384)
    max_parallel_tasks: int = Field(default=4, ge=1, le=4)
    max_pending_jobs: int = Field(default=32, ge=1, le=128)

    @classmethod
    def from_env(cls):
        return cls.model_validate(
            {
                key: os.environ["HARNESS_" + key.upper()]
                for key in cls.model_fields
                if "HARNESS_" + key.upper() in os.environ
            }
        )


class HarnessStopped(BaseException):
    """Control-flow stop must bypass agent Exception handlers and rule fallbacks."""

    def __init__(self, reason, status="failed"):
        super().__init__(reason)
        self.reason, self.status = reason, status


class ToolPolicyError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def resource_versions():
    from repositories import MACHINE_FILE, TOOL_FILE

    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
        for p in (MACHINE_FILE, TOOL_FILE)
    }


def execution_manifest(request):
    from llm_client import runtime_identity

    return {
        "harness_version": HARNESS_VERSION,
        "request_digest": digest(request),
        "runtime": runtime_identity(),
        "resources": resource_versions(),
    }


def _event(kind, **fields):
    return {"at": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}


def pending_job_count(store):
    return sum(
        job["status"] in {"queued", "running", "cancelling"}
        or job.get("harness", {}).get("active", False)
        for job in store.jobs.values()
    )


class RunControl:
    """Shared by graph worker contexts; every reservation is atomic in the job store."""

    def __init__(self, store, job_id, policy=None, resources=None):
        self.store, self.job_id = store, job_id
        self.policy = policy or HarnessPolicy.from_env()
        self.invocation_id = uuid4().hex
        self.started = time.monotonic()
        self.previous_seconds = 0.0
        self.resources = resources

    def start(self):
        with self.store.lock:
            job = self.store.get(self.job_id)
            self._cancel_check(job)
            previous = job.get("harness", {})
            if previous.get("active"):
                raise ValueError("A workflow invocation is already active for this job")
            manifest = execution_manifest(job["request"])
            if self.resources is not None and manifest["resources"] != self.resources:
                raise HarnessStopped("resource_configuration_changed", "interrupted")
            if previous.get("manifest") and previous["manifest"] != manifest:
                raise HarnessStopped("execution_configuration_changed", "interrupted")
            if previous.get("policy"):
                self.policy = HarnessPolicy.model_validate(previous["policy"])
            pending = pending_job_count(self.store)
            if (
                pending + (job["status"] not in {"queued", "running", "cancelling"})
                > self.policy.max_pending_jobs
            ):
                raise ValueError("Execution queue is full; retry after another job finishes.")
            self.previous_seconds = previous.get("active_seconds", 0)
            self.started = time.monotonic()
            record = {
                **previous,
                "manifest": manifest,
                "policy": self.policy.model_dump(),
                "run_id": previous.get("run_id", uuid4().hex),
                "invocation_id": self.invocation_id,
                "invocations": previous.get("invocations", 0) + 1,
                "active": True,
                "phase": "running",
                "usage": previous.get("usage", {"nodes": 0, "model_calls": 0, "tool_calls": 0}),
                "active_seconds": self.previous_seconds,
                "events": [
                    *previous.get("events", []),
                    _event("invocation_started", invocation_id=self.invocation_id),
                ][-200:],
            }
            self.store.update(self.job_id, harness=record)

    @staticmethod
    def _cancel_check(job):
        if job.get("cancel_requested") or job["status"] == "cancelled":
            raise HarnessStopped("cancel_requested", "cancelled")

    def _check_locked(self, job):
        self._cancel_check(job)
        if (
            self.previous_seconds + time.monotonic() - self.started
            >= self.policy.max_active_seconds
        ):
            raise HarnessStopped("active_time_budget_exhausted")

    def check(self):
        with self.store.lock:
            self._check_locked(self.store.get(self.job_id))

    def reserve(self, kind, name):
        limit = {
            "nodes": self.policy.max_nodes,
            "model_calls": self.policy.max_model_calls,
            "tool_calls": self.policy.max_tool_calls,
        }[kind]
        with self.store.lock:
            job = self.store.get(self.job_id)
            self._check_locked(job)
            record = job["harness"]
            if record["usage"][kind] >= limit:
                raise HarnessStopped(kind + "_budget_exhausted")
            record["usage"][kind] += 1
            record["events"] = [*record["events"], _event("reservation", resource=kind, name=name)][
                -200:
            ]
            self.store.update(self.job_id, harness=record)

    def model_options(self, timeout, output_tokens):
        self.reserve("model_calls", "chat")
        remaining = (
            self.policy.max_active_seconds
            - self.previous_seconds
            - (time.monotonic() - self.started)
        )
        return (
            max(
                0.001,
                min(
                    timeout or self.policy.model_timeout_seconds,
                    self.policy.model_timeout_seconds,
                    remaining,
                ),
            ),
            min(output_tokens or self.policy.model_output_tokens, self.policy.model_output_tokens),
        )

    def finish(self, phase, reason=None):
        with self.store.lock:
            record = self.store.get(self.job_id).get("harness", {})
            # Never close another invocation's lease.
            if record.get("invocation_id") != self.invocation_id:
                return
            record.update(
                active=False,
                phase=phase,
                active_seconds=round(self.previous_seconds + time.monotonic() - self.started, 4),
            )
            record["events"] = [
                *record["events"],
                _event("invocation_finished", phase=phase, reason=reason),
            ][-200:]
            self.store.update(self.job_id, harness=record)


def current_control():
    return _control.get()


def check_control():
    if control := current_control():
        control.check()


def invoke(workflow, graph_input, config=None):
    """Single public graph execution boundary for service and offline evaluation."""
    config = dict(config or {})
    job_id = config.get("configurable", {}).get("thread_id")
    if not job_id:
        raise ValueError("A persistent job thread_id is required")
    from engineering_skills import job_skills

    with job_skills(workflow.store, job_id):
        return _invoke(workflow, graph_input, config, job_id)


def _invoke(workflow, graph_input, config, job_id):
    control = RunControl(workflow.store, job_id, resources=workflow.resource_manifest)
    control.start()
    config["recursion_limit"] = control.policy.max_nodes + 1
    config["max_concurrency"] = control.policy.max_parallel_tasks
    token = _control.set(control)
    try:
        result = workflow.graph.invoke(graph_input, config=config)
        control.check()
        if not result.get("__interrupt__") and result.get("status") not in {
            "completed",
            "failed",
            "resource_mismatch",
        }:
            raise ValueError("Graph ended without an accepted terminal state")
        if result.get("status") == "completed" and result.get("verification", {}).get(
            "conclusion"
        ) not in {"pass", "conditional_pass"}:
            raise ValueError("Completed planning requires an explicit verification verdict")
        control.finish("waiting" if result.get("__interrupt__") else "finished")
        return result
    except HarnessStopped as exc:
        control.finish(exc.status, exc.reason)
        raise
    except BaseException as exc:
        control.finish("error", type(exc).__name__)
        raise
    finally:
        _control.reset(token)


def classify_failure(exc):
    """Retry only transport/rate-limit/server failures; never contract/policy defects."""
    if isinstance(exc, ToolPolicyError):
        return "tool_policy", False
    if isinstance(exc, (TimeoutError, ConnectionError)) or type(exc).__name__ in {
        "APITimeoutError",
        "APIConnectionError",
        "ConnectTimeout",
        "ReadTimeout",
        "ConnectError",
    }:
        return "transient_transport", True
    if getattr(exc, "status_code", None) in {408, 429, 500, 502, 503, 504}:
        return "transient_provider", True
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "contract_or_validation", False
    return "worker_error", False


def execute_action(workflow, job_id, action, publish=None):
    """Atomic route review uses the same budgets but preserves the published result on failure."""
    from engineering_skills import job_skills

    with job_skills(workflow.store, job_id):
        return _execute_action(workflow, job_id, action, publish)


def _execute_action(workflow, job_id, action, publish=None):
    control = RunControl(workflow.store, job_id, resources=workflow.resource_manifest)
    try:
        control.start()
    except HarnessStopped as exc:
        raise ValueError("Route review stopped: " + exc.reason) from exc
    token = _control.set(control)
    try:
        result = action()
        control.check()
        if publish:
            result = publish(result)
        control.finish("action_finished")
        return result
    except HarnessStopped as exc:
        control.finish(exc.status, exc.reason)
        raise ValueError("Route review stopped: " + exc.reason) from exc
    except BaseException as exc:
        control.finish("error", type(exc).__name__)
        raise
    finally:
        _control.reset(token)
