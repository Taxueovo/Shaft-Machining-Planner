# shaftmachiningplanner API reference

Version 1.4.0. The backend serves the local API at `http://127.0.0.1:8001`. The frontend uses an HTTP proxy to the same service layer. Deployment is loopback-only and single-process.

## Authentication

Business endpoints require the `x-local-api-token` header; `/health` is exempt. The unified launcher generates credentials. Separate service startup requires a nonempty `LOCAL_API_TOKEN` shared by frontend and backend.

For the examples below, set the shell variable `LOCAL_API_TOKEN` to the token used by your running services. Keep its value outside Git and screenshots.

## Endpoints

| Method | Backend path | Behavior |
| --- | --- | --- |
| POST | `/api/v1/jobs` | Create a job; optional `Idempotency-Key` of 1–128 characters |
| GET | `/api/v1/jobs` | List history with `status`, `search`, `limit`, and `offset` |
| GET | `/api/v1/jobs/{job_id}` | Status, progress, pending input, result readiness, and harness summary |
| GET | `/api/v1/jobs/{job_id}/input` | Retrieve original input for a new job |
| POST | `/api/v1/jobs/{job_id}/choices` | Validate offered feature choices and resume |
| POST | `/api/v1/jobs/{job_id}/engineering` | Validate engineering answers and resume |
| POST | `/api/v1/jobs/{job_id}/cancel` | Request cooperative cancellation; repeated requests return current status |
| GET | `/api/v1/jobs/{job_id}/harness` | Run identity, cumulative budgets, usage observations, and failure summary |
| GET | `/api/v1/jobs/{job_id}/result` | Read the current result; returns a conflict while unavailable |
| POST | `/api/v1/jobs/{job_id}/process-route/customize` | Review a candidate route and publish a successful revision |
| POST | `/api/v1/jobs/{job_id}/process-card/export` | Generate an Excel process draft |
| GET | `/api/v1/jobs/{job_id}/process-card/download` | Download the generated draft |
| GET | `/api/v1/system` | Local configuration and task overview |
| GET | `/api/v1/engineering-skills` | Shipped procedure catalog, versions, contents, and digest |
| GET | `/api/v1/experiences` | Local lesson cards; optional `source_job_id` filter |
| POST | `/api/v1/jobs/{job_id}/experiences` | Propose a lesson bound to the displayed route fingerprint |
| POST | `/api/v1/experiences/{experience_id}/review` | Record an approval, rejection, or retirement using an expected version |

Request fields and validation are defined in `backend/models/` and `backend/app.py`. Frontend proxy paths use `/api/jobs/...`; backend paths use `/api/v1/jobs/...`.

## Create and inspect a job

The [sample request](examples/minimal-shaft.json) uses 45 steel, a solid 50 mm blank, one segment of diameter 30 × length 100 mm, no additional features, and an explicit no-heat-treatment requirement. It is a synthetic API example.

```bash
curl -X POST http://127.0.0.1:8001/api/v1/jobs \
  -H "x-local-api-token: $LOCAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: synthetic-shaft-example-001" \
  --data-binary @docs/examples/minimal-shaft.json
```

HTTP 202 returns `job_id`, the actual `status`, and an acceptance message. Reusing a key with the same input returns the existing job; different input returns HTTP 409. Deduplication lasts while the associated task record is retained.

Replace `JOB_ID` with the returned identifier:

```bash
curl http://127.0.0.1:8001/api/v1/jobs/JOB_ID \
  -H "x-local-api-token: $LOCAL_API_TOKEN"
curl http://127.0.0.1:8001/api/v1/jobs/JOB_ID/harness \
  -H "x-local-api-token: $LOCAL_API_TOKEN"
```

Request the result when `result_ready=true`. Progress percentage alone does not establish successful completion.

## Human continuation and cancellation

Use the status response's `pending_choices` or `pending_engineering` to construct answers with the actual offered identifiers. Continuation reuses the run identity, saved context, checkpoint, and cumulative budgets. The service validates waiting state and rejects incompatible, completed, or cancelled jobs.

```bash
curl -X POST http://127.0.0.1:8001/api/v1/jobs/JOB_ID/cancel \
  -H "x-local-api-token: $LOCAL_API_TOKEN"
```

After `cancelling`, poll until `cancelled`. In-flight functions or model requests return or time out before the cooperative stop completes. Input and evidence are retained; replanning uses a new job.

## Errors and observability

Results include `engineering_skills`, `trace_grading`, and `verification.process_state.counterexamples`. Specialist reports contain complete evidence, source manifests, and the selected procedure checksum. These records can contain business data.

### Experience proposals and decisions

Proposal fields are `title` (1–120 characters), `lesson` (10–1,600 characters), and the current 64-character `route_fingerprint`. Use `agent_collaboration.route_fingerprint` from the result response. Proposals require a completed or failed task with a route and no active execution.

Review fields are `expected_version`, `decision` (`approved`, `rejected`, or `retired`), `reviewer`, `source_reference`, `rationale`, and `valid_until`. Approval requires a future timezone-aware ISO timestamp. The transition graph permits proposed → approved/rejected and approved → retired. Changed versions or source routes return HTTP 409.

Reviewer names are supplied by the authenticated local operator. A recorded approval permits scoped reference retrieval in future tasks; it retains the engineering release requirement. The frontend offers the same workflow under **Engineering Experience**. See [Engineering agent extensions](engineering-agents.md) for matching, retention, and snapshot behavior.

| HTTP status | Typical cause | Action |
| --- | --- | --- |
| 401 | Missing or mismatched local token | Verify frontend/backend credentials |
| 404 | Job unavailable | Check ID, database path, and retention |
| 409 | State conflict, invalid continuation, queue capacity, or idempotency conflict | Inspect current state and correct the request |
| 422 | Invalid request fields, types, or ranges | Correct material, geometry, or request structure |

`execution.policy` records the job's saved policy; `configured_policy` reports the current environment. These may differ after configuration changes. Missing usage observations remain unknown.

Harness summaries omit credentials and full drawing text. Input and result endpoints may contain business data and should remain within the controlled local environment. See [Architecture and harness](langgraph-harness.md) and the [user guide](software-guide.md).
