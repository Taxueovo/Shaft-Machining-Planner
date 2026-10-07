# Industrial-agent research and implementation roadmap

Research snapshot: 2026-10-06 (Asia/Shanghai). Implementation update: 2026-10-07, version 1.3.0. External repository versions below retain the original research snapshot; implementation status reflects the subsequent local upgrade.

## Implementation status

| Area | Local implementation | Next evidence required |
| --- | --- | --- |
| Process-state checks | Geometry and operation-state rules in verification | Broader domain rules and engineer-approved counterexamples |
| Traces and failure feedback | Usage observations, 11 synthetic cases, failure reports, evaluation runner | Live-model baseline and reviewed dataset |
| Optional GEPA | Restricted PromptProfile candidates in a separate environment | Measured gains, cost, and engineering review |
| Historical context | Tencent v3 read-only adapter and initial snapshot | Live-service acceptance, provenance, and retrieval quality |
| Harness and routing | Cumulative budgets, continuation, cancellation, idempotency, evidence-triggered replanning | Distributed coordination and factory acceptance |
| Software workbench | Task center, drafts, input reuse, and system status | Multi-user authorization, packaging, and operational support |

See the [user guide](../docs/software-guide.md), [architecture reference](../docs/langgraph-harness.md), [evaluation protocol](../evaluation/README.md), and [memory assessment](tencentdb-agent-memory-integration.md). Factory feasibility and real-model optimization gains remain unmeasured.

## Architectural priorities

The current system combines LangGraph, a constrained Planner, task contracts, parallel specialist reviews, evidence references, deterministic checks, selective recomputation, and SQLite checkpoint recovery. Development priorities are measurable evaluation, broader process-state constraints, and verified resource data.

Two graphs have distinct responsibilities:

- **Agent task DAG:** coordinates route generation, resource matching, and specialist review. Implemented in the current workflow.
- **Manufacturing operation graph:** represents operation precedence, machine occupancy, setup, shifts, durations, and delivery constraints. This requires a separate scheduling model and site data.

## Research method

The review inspected the project's README, workflow, scheduler, Planner, specialists, tool registry, RAG interfaces, model client, and related tests. External research examined public GitHub metadata, file trees, and READMEs for nine repositories, plus six source files across three repositories. External code was reviewed statically; execution and CI validity were outside that review.

## GitHub candidates

| Repository | Relevant technique | Integration point | Priority and constraints |
| --- | --- | --- | --- |
| [rwth-iat/MP-LLM](https://github.com/rwth-iat/MP-LLM) | LLM proposals, finite-state checks, BFS, optimization fallback | Represent shaft operations with preconditions and state transitions | High. Original domain is ISA-88 modular process equipment; shaft rules need independent domain modeling and validation |
| [gepa-ai/gepa](https://github.com/gepa-ai/gepa) | Trace-based prompt evolution and Pareto candidate search | Offline Planner/specialist prompt candidates scored from failures | High. Requires trusted data, a frozen test set, and explicit promotion |
| [aws-samples/sample-ukg-for-mfg](https://github.com/aws-samples/sample-ukg-for-mfg) | Discovery/Explorer roles, ISA-95 mappings, cross-system queries | Semantic registry linking operations, resources, work orders, and maintenance | Medium-high. Review field mappings and adapt data contracts to local systems |
| [OPCFoundation/UA-.NETStandard/McpServer](https://github.com/OPCFoundation/UA-.NETStandard/tree/master/Applications/McpServer) | OPC UA browsing, reads, history, and subscriptions exposed through MCP | Timestamped equipment and maintenance observations | Medium. The module also exposes writes, methods, and configuration; enforce server-side read-only allowlists and least privilege |
| [JoMinsu-KU/A2M](https://github.com/JoMinsu-KU/A2M) | AAS metadata, MCP tools, manufacturing-process discovery | Asset capability descriptions and reviewed tool registration | Medium. Public assets are partial; source includes PLC writes. Reported deployment results require independent reproduction |
| [ekhurtado/SMIA](https://github.com/ekhurtado/smia) | AAS-based industrial agents and capability/service discovery | Common asset semantics and asset-side negotiation | Medium. Industrial MAS/digital-twin architecture; GPL-3.0 code reuse requires license review |
| [OpenFactoryTwin/ofact](https://github.com/OpenFactoryTwin/ofact) | Factory state models with orders, resources, processes, and events | Failure, urgent-order, and subcontracting scenarios | Later. Requires calibrated durations, shifts, and failure data |
| [aimclub/SAMPO](https://github.com/aimclub/SAMPO) | Resource-constrained graphs, HEFT, genetic algorithms, multi-objective scheduling | Independent manufacturing scheduler and infeasibility explanations | Later. Shaft-shop constraints need explicit extensions and tests |
| [microsoft/agent-lightning](https://github.com/microsoft/agent-lightning) | Harness interaction capture and reinforcement learning | Local-model training after a validated reward/data pipeline | Deferred. v1.0 GPU/verl/vLLM dependencies introduce a separate training stack |

## Source evidence

### Process-state validation

- [MP-LLM validator](https://github.com/rwth-iat/MP-LLM/blob/1edb61b265d2674861821ecd0c489ab9e7132f28/ModPlant_ui_lib/ModPlant_fsa_checker_core.py): `check_prediction_against_rules`, starting at line 240, matches predictions against its rules.
- [MP-LLM session](https://github.com/rwth-iat/MP-LLM/blob/1edb61b265d2674861821ecd0c489ab9e7132f28/ModPlant_ui_lib/session.py): `step_validator` at line 304 checks a generated sequence prefix within the inference session.

These checks establish feasibility within that repository's plant model. Shaft machining requires its own operation definitions and physical constraints.

### Cross-system semantics

- [AWS registry](https://github.com/aws-samples/sample-ukg-for-mfg/blob/583018d336e18e86f6dc9366c3d0608d7ac65e55/agent-discovery/tools/register.py): `register_equivalences`, starting at line 294, rejects associations involving unregistered systems or tables.
- [AWS query tool](https://github.com/aws-samples/sample-ukg-for-mfg/blob/583018d336e18e86f6dc9366c3d0608d7ac65e55/agent-explorer/tools/query_system.py): includes SQL mutation-keyword rejection, row limits, and OpenAPI GET queries. Full read-only enforcement requires review beyond keyword filtering.

### Equipment interfaces

- [A2M MCP server](https://github.com/JoMinsu-KU/A2M/blob/404955f404bd7239c29052325751093d6f5e4eb7/AI_Agent/mcp_server.py): contains `start_manufacturing`, `set_coil_turn`, and `ModbusTcpClient`.
- [A2M tool wrapper](https://github.com/JoMinsu-KU/A2M/blob/404955f404bd7239c29052325751093d6f5e4eb7/AI_Agent/Tool/tool_wrapper.py): synchronously wraps asynchronous tools. Equipment calls were inspected statically.

## Version snapshot

Dates are the reviewed default-branch commit dates in UTC. They differ from release dates and repository `pushed_at` values. License identifiers are GitHub metadata from the research snapshot.

| Repository | Commit date | Pinned commit | License identifier |
| --- | --- | --- | --- |
| MP-LLM | 2026-07-14 | `1edb61b265d2674861821ecd0c489ab9e7132f28` | MIT |
| sample-ukg-for-mfg | 2026-07-09 | `583018d336e18e86f6dc9366c3d0608d7ac65e55` | MIT-0 |
| A2M | 2025-08-08 | `404955f404bd7239c29052325751093d6f5e4eb7` | Apache-2.0 |
| UA-.NETStandard (whole repository) | 2026-10-06 | `e78c6482958ad431d5a1f11a335bf3630fa6cd2c` | NOASSERTION; inspect source/module licenses |
| GEPA | 2026-10-01 | `fb1ed589fd83372caef499cffc2c73173d3b096b` | MIT |
| Agent Lightning | 2026-09-29 | `d381995396274039f2bb1cbe5ff42ac8067f4e47` | MIT |
| OFacT | 2026-03-10 | `45d044da62e7e3f993845c41b37471ea16bb2771` | Apache-2.0 |
| SAMPO | 2025-10-02 | `81ca965176988d4bfa626d250a16114eead46e3c` | BSD-3-Clause |
| SMIA | 2026-10-05 | `6c66601a1234e76216bbd0ac3d3376abc0fda697` | GPL-3.0 |

## Implementation stages

### 1. Representative evaluation and failure feedback

Integration points: `backend/models/workflow.py`, `backend/llm_client.py`, `backend/agents/planner.py`, and `backend/agents/specialists.py`.

The initial 2026-10-06 review identified a gap in usage/version-linked evaluation records and live-model baselines. Version 1.3.0 now records usage observations and synthetic reports. The next step is an engineer-approved set of representative parts and infeasible counterexamples, scored for:

- Hard-constraint violations and false acceptance of infeasible routes.
- Detection of relevant issues, unsupported recommendations, and citation quality.
- Explicit missing-information handling and fallback on model, retrieval, or tool failure.
- Correct invalidation after repair and reuse of unaffected results.
- End-to-end duration, observed tokens/cost, and required human edits; uncollected fields remain unknown.

Keep part families within one split. Use training/validation for candidate search and freeze the final test set. Preserve prior profiles and rollback configuration. The first optimization scope covers role guidance within fixed rules and permissions.

[Agent evaluation guidance](https://developers.openai.com/api/docs/guides/agent-evals) describes reproducible datasets and trace-based scoring applicable to the existing LangGraph workflow.

### 2. Broader process-state constraints

Integration points: `backend/rules/engine.py`, `backend/models/process.py`, and `backend/workflow/nodes/verification.py`.

Extend the part-state model with stock allowance, datum state, heat-treatment state, and completed features. Define engineer-reviewed preconditions and transitions per operation. Return the conflicting operation, state, and rule when a route fails. Independently recheck bounded repairs.

Any solver should operate on domain-defined operations and constraints. Coverage claims should identify the modeled workholding, tool, material, and physical conditions.

### 3. Factory data and capability semantics

Integration points: `backend/repositories.py`, `backend/workflow/tool_registry.py`, and `backend/agents/specialists.py`.

Start with simulated OPC UA and MES read-only adapters. Standardize asset IDs, capability, status, source, collection time, and data quality. Resource screening should distinguish nominal model capability, current availability, stale observations, and unknown data.

Use AAS and ISA-95 for semantic associations. Models can propose mappings; engineers approve critical fields. MCP standardizes tool interfaces, while source provenance and engineering checks establish trust in the returned data.

### 4. Manufacturing scheduling and scenario simulation

Introduce an operation graph and resource calendars backed by measured or explicitly assumed processing/setup times, precedence, machine occupancy, maintenance, and due dates. A deterministic solver owns feasibility and optimization; agents explain gaps and compare scenarios.

SAMPO and OFacT provide relevant scheduling and event-state concepts. Begin with a bounded scheduling pilot, document parameter sources and assumptions, and identify uncalibrated conditions.

## Technology review criteria

Track process planning, AAS, ISA-95, OPC UA, MES, constrained scheduling, and agent evaluation. Each proposed integration should record its capability, pinned evidence, concrete integration point, data/dependency requirements, license, validation method, and rollback procedure.

A weekly review cadence is suitable for identifying material developments. Trials belong in isolated branches; default-path changes require benchmark results and engineering review. Selection should follow project requirements and reproducible evidence.
