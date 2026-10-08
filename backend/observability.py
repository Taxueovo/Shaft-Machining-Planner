"""Thread/context-local model telemetry without prompts, credentials or raw errors."""

from __future__ import annotations

from contextvars import ContextVar

_model_calls = ContextVar("model_call_records", default=None)


def begin_model_capture(records: list):
    return _model_calls.set(records)


def end_model_capture(token):
    _model_calls.reset(token)


def record_model_call(event: dict):
    records = _model_calls.get()
    if records is not None:
        records.append(event)


def summarize_model_calls(traces: list[dict]) -> dict:
    # A trace may appear in both job persistence and graph state; count each ID once.
    unique = {trace["trace_id"]: trace for trace in traces if trace.get("trace_id")}
    calls = [call for trace in unique.values() for call in trace.get("model_calls", [])]
    observed = [call["usage"] for call in calls if call.get("usage") is not None]
    complete = bool(calls) and len(observed) == len(calls)
    return {
        "call_count": len(calls),
        "failed_calls": sum(call.get("status") == "error" for call in calls),
        "usage_observed_calls": len(observed),
        "usage_complete": complete,
        "observed_prompt_tokens": sum(u.get("prompt_tokens") or 0 for u in observed),
        "observed_completion_tokens": sum(u.get("completion_tokens") or 0 for u in observed),
        "total_tokens": sum(u["total_tokens"] for u in observed)
        if complete and all(u.get("total_tokens") is not None for u in observed)
        else None,
        "request_duration_ms": round(sum(call.get("duration_ms", 0) for call in calls), 2),
        "cost_usd": None,  # Pricing and billed retries are not inferred from token counts.
    }
