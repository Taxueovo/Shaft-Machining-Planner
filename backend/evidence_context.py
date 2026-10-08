"""Bound model-visible evidence while retaining complete, addressable source records."""

from copy import deepcopy
import hashlib
import json

MAX_CONTEXT_CHARS = 48000
MAX_TOOL_VIEW_CHARS = 6000


def encoded(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def preview(value, depth=0):
    """Produce valid JSON with explicit omissions, never sliced JSON documents."""
    if isinstance(value, str):
        return (
            value
            if len(value) <= 900
            else {"preview": value[:900], "omitted_characters": len(value) - 900}
        )
    if isinstance(value, list):
        count = 3 if depth else 5
        return {
            "items": [preview(item, depth + 1) for item in value[:count]],
            "total_items": len(value),
            "omitted_items": max(0, len(value) - count),
        }
    if isinstance(value, dict):
        return {key: preview(item, depth + 1) for key, item in value.items()}
    return value


class EvidenceLedger:
    def __init__(self, records, protected=("input", "route", "heat_decision", "task_contract")):
        self.records = deepcopy(records)
        self.protected = set(protected)

    def add(self, evidence_id, value):
        self.records[evidence_id] = deepcopy(value)

    def model_view(self):
        records = {}
        for eid, value in self.records.items():
            if eid.startswith("skill:"):
                # Procedure text is already in the system prompt; expose identity once.
                records[eid] = {key: item for key, item in value.items() if key != "instructions"}
                continue
            full = encoded(value)
            if eid in self.protected or len(full) <= MAX_TOOL_VIEW_CHARS:
                records[eid] = value
            else:
                projected = preview(value)
                if len(encoded(projected)) > MAX_TOOL_VIEW_CHARS:
                    projected = {"status": "offloaded", "read_evidence_required": True}
                records[eid] = {
                    "content": projected,
                    "complete": False,
                    "source_characters": len(full),
                    "digest": hashlib.sha256(full.encode()).hexdigest(),
                    "retrieval": {"tool": "read_evidence", "evidence_id": eid},
                }
        if len(encoded(records)) > MAX_CONTEXT_CHARS:
            raise ValueError("Evidence context limit exceeded; narrow the task before model review")
        return records

    def read(self, evidence_id, path=(), offset=0, limit=5):
        if evidence_id not in self.records:
            raise ValueError("Evidence was not acquired in this review")
        value = self.records[evidence_id]
        for key in path:
            if not isinstance(value, dict) or key not in value:
                raise ValueError("Unknown evidence path")
            value = value[key]
        if isinstance(value, list):
            content = value[offset : offset + limit]
            result = {
                "items": content,
                "offset": offset,
                "total_items": len(value),
                "next_offset": offset + len(content)
                if offset + len(content) < len(value)
                else None,
            }
        elif isinstance(value, str):
            # Text pages use characters; the schema documents this difference.
            content = value[offset : offset + limit * 600]
            result = {
                "text": content,
                "offset": offset,
                "total_characters": len(value),
                "next_offset": offset + len(content)
                if offset + len(content) < len(value)
                else None,
            }
        else:
            result = {"content": value}
        if len(encoded(result)) > MAX_TOOL_VIEW_CHARS:
            raise ValueError("Evidence page too large; select a narrower path or smaller limit")
        return deepcopy({"source_evidence_id": evidence_id, "path": list(path), **result})

    def manifest(self):
        return [
            {
                "evidence_id": eid,
                "characters": len(encoded(value)),
                "digest": hashlib.sha256(encoded(value).encode()).hexdigest(),
            }
            for eid, value in self.records.items()
        ]

    def reference_only(self, evidence_id):
        """Paging reference data preserves its authority; a tool ID cannot promote it."""
        if evidence_id.startswith(("memory:", "experience:", "skill:")):
            return True
        record = self.records[evidence_id]
        if isinstance(record, dict) and record.get("source_evidence_id"):
            return self.reference_only(record["source_evidence_id"])
        return False
