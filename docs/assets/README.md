# Documentation assets

Captured for version 1.3.0 on 2026-10-07. The JPEG screenshots are actual browser captures of the Simplified Chinese interface, using an isolated synthetic task database, rules mode, and disabled external memory. Capture data excludes customer drawings, accounts, and credentials.

Version 1.4.0 additions were captured on 2026-10-08 in a separate synthetic database. The lesson, reviewer, and verification reference shown in the experience screenshots are explicitly labeled QA examples. Tencent retrieval and model calls remained disabled. The architecture SVG was updated for the new procedure, evidence, and local-experience components.

| File | Content | Capture context |
| --- | --- | --- |
| workbench.jpg | Summary and recent tasks | Three synthetic task records |
| part-input.jpg | Material, blank, and stepped-shaft form | Loaded example before submission |
| task-center.jpg | Search, filters, and input reuse | Same isolated workspace |
| process-result.jpg | Deterministic verification | Simple shaft without additional features; conditional pass |
| process-route.jpg | Operations and resource candidates | Same shaft with public machine/tool samples |
| runtime-harness.jpg | Cumulative budgets and event access | Initial planning plus one route review; zero model requests in rules mode |
| system-status.jpg | Runtime mode, resources, optional modules | Memory disabled; local configuration view |
| system-architecture.svg | Component responsibilities | Authored from the current implementation; routing detail in the architecture reference |
| engineering-evidence.jpg | Process-state coverage and execution diagnostics | Version 1.4.0; synthetic rules-mode task with incomplete explicit dimensions |
| engineering-experience.jpg | Reviewed lesson and local decision controls | Version 1.4.0; synthetic QA review followed by cross-task recall |

The README hero at `.github/assets/readme-hero.svg` is an engineering illustration. Both SVGs are editable repository sources without external scripts or font requests.

## Recapturing screenshots

1. Create an isolated demonstration database.
2. Set `LLM_PROVIDER=rules` and `AGENT_MEMORY_ENABLED=false`.
3. Load the example or enter clearly identified synthetic input.
4. Inspect each page and capture JPEG screenshots with UI context and actual statuses intact.
5. Update captions, alternative text, software version, and provenance alongside the images.

Keep private task stores, full model inputs, and credentials outside public assets. Counters, timings, and IDs in a screenshot describe that individual demonstration.
