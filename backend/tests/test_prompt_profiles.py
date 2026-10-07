from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from agents.prompts import PromptManager
from models.tasks import TaskPlan
from prompt_profiles import (
    BASELINE,
    PromptProfile,
    augment_instructions,
    load_profile,
    profile_metadata,
    use_profile,
)
from service import _request_cache_key
from workflow.task_scheduler import current_results
from tests.test_production_reviews import workflow_state


@pytest.mark.parametrize(
    "changes",
    [
        {"additions": {"execute_shell": "do work"}},
        {"additions": {"planner": "x" * 4001}},
        {"override_permissions": True},
    ],
)
def test_profile_cannot_define_unregistered_capabilities(changes):
    with pytest.raises(ValidationError):
        PromptProfile(name="test", version="1", **changes)


def test_profile_appends_guidance_and_scope_restores_on_failure():
    manager = PromptManager()
    manager.register("repair", system="Preserve required checks", user="{route}")
    candidate = PromptProfile(
        name="candidate", version="1", additions={"repair": "Inspect operation references"}
    )
    with pytest.raises(RuntimeError):
        with use_profile(candidate):
            text = manager.render_messages("repair", {"route": "data"})[0]["content"]
            assert "Preserve required checks" in text and "Inspect operation references" in text
            assert "cannot override" in text
            raise RuntimeError()
    assert augment_instructions("repair", "base") == "base"
    assert profile_metadata() == profile_metadata(BASELINE)


def test_profile_changes_invalidate_both_task_and_request_cache():
    _, _, state = workflow_state()
    plan = TaskPlan.model_validate(state["task_plan"])
    initial_key = _request_cache_key(state["request"])
    assert current_results(plan, state)
    with use_profile(
        PromptProfile(
            name="candidate", version="2", additions={"planner": "Check existing results"}
        )
    ):
        assert not current_results(plan, state)
        assert _request_cache_key(state["request"]) != initial_key


def test_profile_content_digest_changes_without_version_rename(tmp_path):
    original = BASELINE.model_dump()
    changed = deepcopy(original)
    changed["additions"] = {"planner": "Changed guidance"}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(changed))
    assert profile_metadata(load_profile(path))["digest"] != profile_metadata(BASELINE)["digest"]
