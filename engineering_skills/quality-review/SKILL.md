---
name: quality-review
description: Review drawing coverage, tolerances, inspection order, and route-bound evidence for shaft plans.
version: 1.0.0
---

# Quality review procedure

1. Match every input segment and feature to the current route. Check declared final diameter states against supplied drawing limits.
2. Separate nominal dimensions, explicit deviations, surface-finish requirements, and unspecified acceptance limits. Request missing requirements rather than assigning tolerances.
3. Use `inspect_route` to check final inspection placement and packaging prerequisites. Any modification after final inspection requires a route correction.
4. Record the drawing revision, datum system, inspection characteristics, and measuring-equipment evidence that remain unconfirmed.
5. Cite current input, route, or retrieved evidence. Historical experience and procedure documents guide review; current drawing and inspection acceptance require independent confirmation.

Use `read_evidence` for omitted reference pages. Return operation-specific corrections where supported and engineering questions where evidence is incomplete. Retain the engineering release requirement.
