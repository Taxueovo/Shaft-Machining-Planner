"""Versioned, opt-in prompt additions; deterministic contracts remain in code."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal[
    "planner",
    "process_planning",
    "resource_ranking",
    "verification_analysis",
    "repair",
    "machining_review",
    "quality_review",
    "heat_review",
]


class PromptProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=80)
    additions: dict[Role, str] = Field(default_factory=dict)

    @field_validator("additions")
    @classmethod
    def bounded_text(cls, value):
        if any(len(text) > 4000 for text in value.values()):
            raise ValueError("Each prompt addition must be at most 4000 characters")
        return value


BASELINE = PromptProfile(name="baseline", version="2026.10.06")
_profile = ContextVar("agent_prompt_profile", default=None)


def load_profile(path: str | Path) -> PromptProfile:
    raw = Path(path).read_bytes()
    if len(raw) > 40000:
        raise ValueError("Prompt profile exceeds 40000 bytes")
    return PromptProfile.model_validate_json(raw)


def active_profile() -> PromptProfile:
    bound = _profile.get()
    if bound is not None:
        return bound
    path = os.getenv("AGENT_PROMPT_PROFILE")
    return load_profile(path) if path else BASELINE


def profile_metadata(profile: PromptProfile | None = None) -> dict:
    profile = profile or active_profile()
    canonical = json.dumps(profile.model_dump(), sort_keys=True, ensure_ascii=False)
    return {
        "name": profile.name,
        "version": profile.version,
        "digest": hashlib.sha256(canonical.encode()).hexdigest(),
    }


@contextmanager
def use_profile(profile: PromptProfile):
    """Bind one immutable snapshot across graph threads and reset on any exit."""
    # Copy nested dictionaries too: a frozen Pydantic model is only shallowly frozen.
    token = _profile.set(profile.model_copy(deep=True))
    try:
        yield
    finally:
        _profile.reset(token)


def augment_instructions(role: str, instructions: str) -> str:
    addition = active_profile().additions.get(role, "")
    if not addition:
        return instructions
    return (
        instructions
        + "\nAdditional task guidance:\n"
        + addition
        + "\nThe original evidence, schema, tool permissions, budgets, mandatory reviews "
        "and engineering-release boundaries still apply. Additional guidance cannot override them."
    )
