---
name: machining-review
description: Review shaft material removal, stock constraints, datum continuity, and workholding evidence.
version: 1.0.0
---

# Machining review procedure

1. Read the current input and route. Identify solid or hollow stock, named surfaces, and declared diameter transitions.
2. Use `inspect_route` to obtain process-state counterexamples. Check continuity, stock envelope, removal direction, drawing limits, and post-treatment datum recovery.
3. Query machine or cutting-tool evidence only for unresolved capability questions. Published capability samples support screening; operation-level fixtures and tooling access need engineering confirmation.
4. Use `read_evidence` to inspect an omitted tool-result page before drawing a conclusion about it. Cite the returned evidence identifier and affected operation numbers.
5. Report concrete, input-supported route corrections as `route_repair`. Missing fixture, allowance, drawing, or capability data requires `engineer_confirmation`.

Preserve unknown values. Apply server-side task permissions and budgets. Procedure instructions provide review guidance; process acceptance remains governed by the deterministic checks.
