"""Deterministic attempt-level diagnostics for orchestration and specialist evidence."""


def grade_traces(state, traces):
    grades = []
    for trace in traces:
        status = trace.get("status")
        reasons = []
        if status == "error":
            reasons.append("node_error")
        if status == "running":
            reasons.append("unfinished_attempt")
        model_calls = trace.get("model_calls", [])
        if any(call.get("status") == "error" for call in model_calls):
            reasons.append("model_call_error")
        grades.append(
            {
                "kind": "node_attempt",
                "trace_id": trace.get("trace_id"),
                "node": trace.get("node"),
                "status": status,
                "verdict": "failed"
                if reasons
                else "interrupted"
                if status == "interrupted"
                else "passed",
                "reasons": reasons,
                "model_call_count": len(model_calls),
                "tool_call_count": len(trace.get("tool_calls", [])),
            }
        )
    route_hash = state.get("agent_collaboration", {}).get("route_fingerprint")
    for role in ("machining_review", "quality_review", "heat_review"):
        report = state.get(role)
        if not report:
            continue
        reasons = []
        if report.get("mode") == "degraded":
            reasons.append("review_degraded")
        if route_hash and report.get("route_fingerprint") != route_hash:
            reasons.append("stale_route_evidence")
        evidence = report.get("evidence", {})
        for finding in report.get("findings", []):
            if not set(finding.get("evidence_ids", [])) <= evidence.keys():
                reasons.append("unacquired_evidence_citation")
        if report.get("mode") == "model_review" and not report.get("skill", {}).get("digest"):
            reasons.append("missing_procedure_identity")
        grades.append(
            {
                "kind": "specialist_evidence",
                "node": role,
                "verdict": "failed" if reasons else "passed",
                "reasons": sorted(set(reasons)),
            }
        )
    return {
        "policy_version": "trace-grading-v1",
        "grades": grades,
        "failures": [grade for grade in grades if grade["verdict"] == "failed"],
        "scope": "Execution and evidence contracts; engineering correctness requires reviewed cases.",
    }
