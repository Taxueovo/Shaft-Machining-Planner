---
name: heat-treatment-review
description: Review requested heat treatment, material condition, datum recovery, and post-treatment machining.
version: 1.0.0
---

# Heat-treatment review procedure

1. Read the explicit heat-treatment requirement. When it is `none`, record the review as not applicable.
2. Compare the requested treatment with the recorded heat-treatment decision and current route. Identify missing hardness scale, case depth, treated surfaces, and initial material condition.
3. Use `inspect_route` to check the treatment and datum-recovery sequence. Check that declared removal preserves supplied dimensional requirements.
4. Request approved provider capability and the treatment specification. The local cutting-tool and machine sample does not qualify a furnace or subcontractor.
5. Cite input and decision evidence. Confirm the applicability of historical experience to material, stock type, and treatment before referring to it.

Use `read_evidence` to inspect omitted reference content. Keep treatment recipes and production approval subject to engineering review.
