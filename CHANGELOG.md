# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

- **peagent** — structured machining process planning for motor shafts:
  - Input validation and process-route planning via a LangGraph workflow
  - Machine tool / cutting tool capability libraries with resource verification
  - Process rules engine (sequence, dependencies, heat treatment)
  - RAG index over specification and case libraries (ChromaDB)
- **frontend** — Jinja2 web UI with dynamic forms, status polling, and RAG
  management pages
- Tests for the backend route engine, models, and resource verification
- MIT license, contributing guide, code of conduct, and security policy
