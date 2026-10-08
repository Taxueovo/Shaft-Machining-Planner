"""Run the actual workflow, grade backend state, and preserve actionable failures."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator, ValidationError
from typing import Literal

from models.workflow import PlanningRequest
from observability import summarize_model_calls
from prompt_profiles import PromptProfile, profile_metadata, use_profile
from llm_client import runtime_identity
from workflow import JobStore, Workflow
from workflow.harness import HarnessStopped
from evaluation.trace_grading import grade_traces


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["completed", "input_rejected", "waiting_input", "failed"] = "completed"
    required_workers: list[str] = Field(default_factory=list)
    forbidden_operations: list[str] = Field(default_factory=list)
    required_operations: list[str] = Field(default_factory=list)
    required_constraint_codes: list[str] = Field(default_factory=list)


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    split: Literal["train", "validation", "test"]
    request: dict
    expected: Expectation


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    evidence_level: Literal["synthetic", "engineer_reviewed"]
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def grouped_splits(self):
        ids, families = set(), {}
        for case in self.cases:
            if case.case_id in ids:
                raise ValueError("Duplicate evaluation case ID")
            ids.add(case.case_id)
            if case.family in families and families[case.family] != case.split:
                raise ValueError("Part families must not cross dataset splits")
            families[case.family] = case.split
        return self


def load_dataset(path: str | Path) -> Dataset:
    return Dataset.model_validate_json(Path(path).read_text())


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def grade_state(case: EvalCase, state: dict, traces: list[dict]) -> list[str]:
    """Backend state and independent per-attempt failures, not an agent's success prose."""
    failures = []
    actual_codes = {
        issue["error_code"] for issue in state.get("verification", {}).get("validation_issues", [])
    }
    if not set(case.expected.required_constraint_codes) <= actual_codes:
        failures.append("Expected constraint counterexample was not reported")
    waiting = bool(state.get("__interrupt__"))
    if case.expected.outcome == "waiting_input":
        if not waiting:
            failures.append("Expected an explicit input interruption")
        return failures
    if case.expected.outcome == "failed":
        if (
            state.get("status") != "failed"
            or state.get("verification", {}).get("conclusion") != "failed"
        ):
            failures.append("Expected the workflow to fail closed")
        if state.get("agent_collaboration", {}).get("production_release"):
            failures.append("A failed workflow incorrectly released production")
        return failures
    if waiting or state.get("status") != "completed":
        failures.append("Workflow did not reach the expected completed planning state")
    if state.get("release_status") != "engineering_review_required":
        failures.append("Engineering release boundary was lost")
    verification = state.get("verification", {})
    if verification.get("conclusion") not in {"pass", "conditional_pass"}:
        failures.append("Final deterministic verification did not pass")
    if any(not check.get("passed") for check in verification.get("checks", [])):
        failures.append("Final state contains a failed mandatory check")
    workers = {task["worker"] for task in state.get("task_plan", {}).get("tasks", [])}
    if not set(case.expected.required_workers) <= workers:
        failures.append("Missing expected worker coverage")
    names = {operation["name"] for operation in state.get("process_route", [])}
    if set(case.expected.forbidden_operations) & names:
        failures.append("Route contains an explicitly forbidden operation")
    if not set(case.expected.required_operations) <= names:
        failures.append("Route omitted an expected operation")
    if state.get("repair_count", 0):
        failures.append(
            "Candidate required route repair; final success must not hide proposal failure"
        )
    if any(
        event.get("mode") in {"degraded", "budget_exhausted"}
        for event in state.get("planner_events", [])
    ):
        failures.append("Planner candidate failed validation or exhausted its budget")
    council = state.get("agent_collaboration", {})
    if council.get("degraded"):
        failures.append("Independent model review degraded")
    if council.get("production_release"):
        failures.append("Review incorrectly released production")
    for report in council.get("reports", []):
        if report.get("route_fingerprint") != council.get("route_fingerprint"):
            failures.append("Review is bound to a different route version")
    for trace in traces:
        if trace.get("status") == "error":
            failures.append(f"Node attempt failed: {trace.get('node')}")
    for grade in grade_traces(state, traces)["failures"]:
        failures.append(
            f"Trace contract failed: {grade.get('node')}: {', '.join(grade['reasons'])}"
        )
    return sorted(set(failures))


def run_case(case: EvalCase) -> dict:
    started = time.perf_counter()
    state, traces, failures = {}, [], []
    store = flow = None
    runtime_harness = None
    try:
        try:
            payload = PlanningRequest.model_validate(case.request).model_dump()
        except ValidationError:
            failures = (
                []
                if case.expected.outcome == "input_rejected"
                else ["Input validation unexpectedly rejected the case"]
            )
            return {
                "case_id": case.case_id,
                "family": case.family,
                "split": case.split,
                "passed": not failures,
                "failures": failures,
                "outcome": "input_rejected",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "model_usage": summarize_model_calls([]),
            }
        if case.expected.outcome == "input_rejected":
            failures.append("Invalid input was accepted")
        store = JobStore(db_path=":memory:")
        job_id = uuid4().hex
        store.create(job_id, payload)
        flow = Workflow(store)
        state = flow.invoke(
            {"job_id": job_id, "request": payload}, {"configurable": {"thread_id": job_id}}
        )
        traces = store.get(job_id).get("execution_trace", [])
        if case.expected.outcome != "input_rejected":
            failures.extend(grade_state(case, state, traces))
    except HarnessStopped as stop:
        failures.append("Runtime harness stopped execution: " + stop.reason)
        state = {"status": stop.status}
        if store is not None:
            traces = store.get(job_id).get("execution_trace", [])
    except Exception as exc:
        failures.append(f"Workflow exception: {type(exc).__name__}")
        if store is not None:
            traces = store.get(job_id).get("execution_trace", [])
    finally:
        if store is not None:
            runtime_harness = store.get(job_id).get("harness")
        if flow is not None:
            flow.checkpoint_connection.close()
        if store is not None:
            store.connection.close()
    return {
        "case_id": case.case_id,
        "family": case.family,
        "split": case.split,
        "passed": not failures,
        "failures": sorted(set(failures)),
        "outcome": "waiting_input" if state.get("__interrupt__") else state.get("status", "error"),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "repair_count": state.get("repair_count", 0),
        "model_usage": summarize_model_calls(traces),
        "runtime_harness": runtime_harness,
        "trace_grading": grade_traces(state, traces),
        "feedback": {
            "validation_issues": state.get("verification", {}).get("validation_issues", []),
            "constraint_counterexamples": state.get("verification", {})
            .get("process_state", {})
            .get("counterexamples", []),
            "verification_message": state.get("verification", {}).get("message"),
            "worker_failures": [
                {"worker": r.get("worker"), "status": r.get("status")}
                for r in state.get("worker_results", {}).values()
                if r.get("status") == "tool_failed"
            ],
            "planner_modes": [event.get("mode") for event in state.get("planner_events", [])],
            "failed_nodes": [
                trace.get("node") for trace in traces if trace.get("status") == "error"
            ],
        },
    }


def run_suite(dataset: Dataset, profile: PromptProfile, splits: set[str], repeats: int = 1) -> dict:
    if not splits <= {"train", "validation", "test"} or not 1 <= repeats <= 20:
        raise ValueError("Invalid evaluation split or repeat count")
    cases = [case for case in dataset.cases if case.split in splits]
    if not cases:
        raise ValueError("Selected evaluation split is empty")
    with use_profile(profile):
        identity = runtime_identity()
        results = [dict(run_case(case), repeat=index) for index in range(repeats) for case in cases]
    passed = sum(result["passed"] for result in results)
    return {
        "schema_version": 1,
        "dataset": dataset.name,
        "dataset_version": dataset.version,
        "dataset_digest": digest(dataset.model_dump()),
        "evidence_level": dataset.evidence_level,
        "splits": sorted(splits),
        "repeats": repeats,
        "execution_identity": identity,
        "profile": profile_metadata(profile),
        "case_count": len(results),
        "passed": passed,
        "behavior_pass_rate": passed / len(results),
        "scope": "Workflow behavior and declared constraints; not physical machining accuracy or production qualification.",
        "results": results,
        "badcases": [result for result in results if not result["passed"]],
    }


def compare_runs(baseline: dict, candidate: dict) -> dict:
    for key in ("schema_version", "dataset_digest", "splits", "repeats"):
        if baseline.get(key) != candidate.get(key):
            raise ValueError(f"Cannot compare runs with different {key}")
    for key in (
        "provider",
        "model",
        "endpoint_digest",
        "pipeline_version",
        "memory",
        "engineering_skills",
    ):
        if baseline["execution_identity"].get(key) != candidate["execution_identity"].get(key):
            raise ValueError(f"Cannot compare runs with different {key}")
    old = {(r["case_id"], r["repeat"]): r for r in baseline["results"]}
    new = {(r["case_id"], r["repeat"]): r for r in candidate["results"]}
    if old.keys() != new.keys():
        raise ValueError("Cannot compare different evaluated cases")
    regressions = [key[0] for key in old if old[key]["passed"] and not new[key]["passed"]]
    exercised = all(
        any(r["model_usage"]["call_count"] for r in run["results"]) for run in (baseline, candidate)
    )
    return {
        "regressions": sorted(set(regressions)),
        "candidate_badcases": len(candidate["badcases"]),
        "model_candidate_exercised": exercised,
        "behavior_gate_passed": not regressions and not candidate["badcases"],
        "ready_for_engineering_review": exercised
        and not regressions
        and not candidate["badcases"]
        and "test" in candidate["splits"],
        "automatic_activation": False,
        "evidence_level": candidate["evidence_level"],
    }
