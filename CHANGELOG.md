# Changelog

## 1.3.0 — 2026-10-07

- Trigger Planner intervention on changes to decision evidence; reuse plans during ordinary scheduling and narrow Send payloads to required input and dependency outputs.
- Add a persistent execution harness with cumulative budgets, context snapshots, run identities, compatibility checks, cooperative cancellation, and failure records.
- Share the execution entry point across services, offline evaluation, and edited-route review.
- Add classified bounded retries, atomic human continuation, queue rollback, idempotent job creation, and queue admission limits.
- Add execution controls and an in-workbench cancellation dialog; record budget stops in evaluation failure reports.
- Document harness contracts, default policies, verification coverage, and deployment boundaries.
- Publish English project documentation with seven browser screenshots of synthetic tasks, editable architecture diagrams, recovery/review/evaluation flows, an API reference, and a validated sample request. Align snapshot, retention, and language conventions with the implementation.

## 1.2.0 — 2026-10-07

- Introduce the local workbench with sidebar navigation, task summaries, recent records, and system status.
- Add persistent task history with name/material/ID search, status filters, pagination, and original-input reuse.
- Add part name, weight, surface treatment, batch size, and manual browser draft save/restore; retain input errors and warn before leaving unsaved changes.
- Mark interrupted execution on startup and preserve human-waiting jobs; disable idle shutdown by default.
- Verify service identity and readiness in the launcher; clean up launcher-owned processes on startup failure.
- Expose engineering drafts and review actions on result pages; stop polling missing jobs and terminal jobs without results.
- Retain the optional read-only Tencent memory adapter, process-state checks, telemetry, and offline evaluation. Memory remains disabled by default; live-service and factory acceptance are pending.
- Add the user guide and regression coverage for history, restart handling, status endpoints, and launcher behavior.

## [0.2.0] - 2026-08-16

### Added

- **RAG retrieval enhancement**:
  - Deterministic feature-penalty reranking — penalize candidates missing the
    query's discriminating feature keywords (thread/spline/hole/gear/chrome...),
    compensating for semantic rerankers' weak feature-level discrimination
  - Cloud rerank via `RERANKER_CLOUD_MODEL` (DashScope qwen3-rerank) with
    graceful fallback to the local CrossEncoder
  - `EMBEDDING_DIMENSIONS` config and batch-size 10 to support text-embedding-v4
  - Long `###` spec subsections are now auto-split by character count

## [0.1.0] - 2026-08-14

Initial public release.

### Added

- **shaftmachiningplanner** — structured machining process planning for motor shafts:
  - Input validation and process-route planning via a LangGraph workflow
  - Machine tool / cutting tool capability libraries with resource verification
  - Process rules engine (sequence, dependencies, heat treatment)
  - RAG index over specification and case libraries (ChromaDB)
- **frontend** — Jinja2 web UI with dynamic forms, status polling, and RAG
  management pages
- Tests for the backend route engine, models, and resource verification
- MIT license, contributing guide, code of conduct, and security policy
