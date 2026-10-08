"""Versioned, read-only engineering procedures with run-scoped snapshots."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "engineering_skills"
ROLE_SKILLS = {
    "machining_review": "machining-review",
    "quality_review": "quality-review",
    "heat_review": "heat-treatment-review",
}
_skills = ContextVar("engineering_skill_snapshot", default=None)


def load_skills():
    """Load only shipped procedures. Skill content cannot register tools or run scripts."""
    items = {}
    for name in sorted(set(ROLE_SKILLS.values())):
        content = (ROOT / name / "SKILL.md").read_text(encoding="utf-8")
        if len(content) > 6000 or not content.startswith("---\n"):
            raise ValueError("Invalid engineering skill package: " + name)
        header, instructions = content[4:].split("\n---\n", 1)
        metadata = dict(line.split(": ", 1) for line in header.splitlines())
        if metadata.get("name") != name or not metadata.get("description"):
            raise ValueError("Invalid skill metadata: " + name)
        checksum = hashlib.sha256(content.encode()).hexdigest()
        items[name] = {
            "name": name,
            "description": metadata["description"],
            "version": metadata["version"],
            "digest": checksum,
            "evidence_id": f"skill:{name}@{checksum[:16]}",
            "instructions": instructions.strip(),
        }
    checksum = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
    return {"digest": checksum, "items": items}


def skill_snapshot():
    return deepcopy(_skills.get() or load_skills())


def skill_identity():
    snapshot = skill_snapshot()
    return {
        "digest": snapshot["digest"],
        "versions": {name: item["version"] for name, item in snapshot["items"].items()},
    }


def skill_for(role):
    return skill_snapshot()["items"][ROLE_SKILLS[role]]


@contextmanager
def use_skills(snapshot):
    token = _skills.set(deepcopy(snapshot))
    try:
        yield
    finally:
        _skills.reset(token)


@contextmanager
def job_skills(store, job_id):
    """Human continuation and route review use the original procedure contents."""
    with store.lock:
        snapshot = store.get(job_id).get("skill_snapshot")
        if snapshot is None:
            snapshot = load_skills()
            store.update(job_id, skill_snapshot=snapshot)
    with use_skills(snapshot):
        yield
