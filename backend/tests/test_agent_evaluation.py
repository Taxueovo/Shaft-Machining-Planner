from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation.harness import (
    Dataset,
    Expectation,
    EvalCase,
    compare_runs,
    grade_state,
    load_dataset,
    run_suite,
)
from prompt_profiles import BASELINE

DATASET = Path(__file__).resolve().parents[2] / "evaluation/cases.synthetic.json"


def test_family_variants_cannot_leak_into_frozen_tests():
    data = load_dataset(DATASET).model_dump()
    data["cases"][1]["family"] = data["cases"][0]["family"]
    data["cases"][1]["split"] = "test"
    with pytest.raises(ValidationError, match="families"):
        Dataset.model_validate(data)


def test_rules_suite_runs_real_workflow_without_claiming_model_improvement():
    report = run_suite(load_dataset(DATASET), BASELINE, {"train", "validation", "test"})
    assert not report["badcases"]
    result = compare_runs(report, report)
    assert result["behavior_gate_passed"]
    assert not result["model_candidate_exercised"]
    assert not result["ready_for_engineering_review"] and not result["automatic_activation"]


def test_success_prose_and_repair_do_not_mask_failed_backend_checks():
    case = EvalCase(
        case_id="probe", family="probe", split="test", request={}, expected=Expectation()
    )
    state = {
        "status": "completed",
        "summary": "Everything succeeded",
        "repair_count": 1,
        "release_status": "engineering_review_required",
        "verification": {"conclusion": "pass", "checks": [{"name": "critical", "passed": False}]},
    }
    failures = grade_state(case, state, [])
    assert any("failed mandatory" in failure for failure in failures)
    assert any("repair" in failure for failure in failures)


def test_comparison_rejects_different_dataset_or_model_and_flags_per_case_regression():
    report = {
        "schema_version": 1,
        "dataset_digest": "data",
        "splits": ["test"],
        "repeats": 1,
        "execution_identity": {"model": "model"},
        "evidence_level": "synthetic",
        "badcases": [],
        "results": [
            {"case_id": "a", "repeat": 0, "passed": True, "model_usage": {"call_count": 1}}
        ],
    }
    candidate = deepcopy(report)
    candidate["dataset_digest"] = "changed"
    with pytest.raises(ValueError, match="dataset_digest"):
        compare_runs(report, candidate)
    candidate = deepcopy(report)
    candidate["execution_identity"]["model"] = "different"
    with pytest.raises(ValueError, match="model"):
        compare_runs(report, candidate)
    candidate = deepcopy(report)
    candidate["results"][0]["passed"] = False
    candidate["badcases"] = [candidate["results"][0]]
    assert compare_runs(report, candidate)["regressions"] == ["a"]
