# ShaftPlanner

[![CI](https://github.com/Taxueovo/ShaftPlanner/actions/workflows/ci.yml/badge.svg)](https://github.com/Taxueovo/ShaftPlanner/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Maintainer](https://img.shields.io/badge/maintainer-Taxueovo-blue)](https://github.com/Taxueovo)

Structured process planning for motor shafts, with one-click CAD import.

ShaftPlanner plans the machining process route (process planning) of stepped shafts —
turning, keyways, splines, gears, bores, heat treatment and finishing — and verifies
the plan against the local machine tool and cutting tool capability libraries.

One project, two subsystems:

- **peagent** (`backend/` + `frontend/`): structured shaft process planning with
  local resource selection (machine tools / cutting tools) and verification.
- **cadagent** (`cadagent/`): multi-modal 3D CAD model analysis that acts as a
  second input path for peagent (upload a STEP/BREP file instead of typing the
  form by hand).

## Authors & Maintainers

- **Taxueovo** — core maintainer and primary developer.
- **Melanie-Fan** — documentation and design.

## 1. Architecture

```
User
 ├─[manual form]──▶ frontend(8000) ──▶ backend(8001) ──▶ process route + resource verification
 └─[upload STEP]▶ cadagent(8100) ── feature extraction + LLM completion ──▶ prefilled form → submit job
```

- `backend/`: input validation, LangGraph workflow, machine tool / cutting tool
  Excel libraries, process rules, verification, RAG.
- `frontend/`: Jinja2 pages, dynamic form, status polling, HTTP proxy,
  "Import from CAD".
- `cadagent/`: STEP/BREP → B-Rep feature extraction + multi-view rendering + CAE
  chat; `/api/v1/planning-input` generates a peagent-compatible `PlanningRequest`
  draft in one step (geometric rule mapping + LLM completion of engineering intent).
- The frontend never imports backend business functions; the processes
  communicate only over HTTP/JSON.

## 2. Features

Implemented:

- Structured entry of material, bar stock and stepped segments; segment-relative
  or global-absolute positioning
- Dynamic feature input: keyway, hole, flat, thread, knurl, bearing seat, spline,
  taper, recess (relief groove), seal area, gear, flange, **bore** (stepped inner bore)
- High-precision feature detection, LangGraph `interrupt` / `Command(resume=...)`
  for human-in-the-loop timing decisions
- Machine tool / cutting tool filtering, base process route generation,
  conditional operation insertion, per-operation resource display,
  rule-based Verification
- RAG (process handbook + case base) injected into the planning workflow
- **CAD import**: upload STEP/BREP → auto-detect shaft segments / keyways / radial
  holes / splines / gears / bores → backfill the form (LLM-suggested fields are
  highlighted for confirmation) → the result page shows the cadagent 3D renders
- **Full field coverage**: the peagent data model carries every field extracted by
  cadagent (keyway type, hole count, spline major/minor diameter, helical gear
  helix angle, stepped bore, etc.)

Not implemented: cost calculation, Word/PDF export, ERP/MES integration,
multi-user support and database persistence.

## 3. Installation

Create the Conda environment and install the Python dependencies:

```bash
conda env create -f environment.yml
conda activate shaftplanner
```

or manually:

```bash
conda create -n shaftplanner python=3.10
conda activate shaftplanner
pip install -r requirements.txt
```

cadagent feature extraction depends on `pythonocc-core` (install separately).

## 4. Running

### One-click start (recommended)

```bash
python start_shaftplanner.py              # starts cadagent(8100) + peagent frontend/backend, opens the browser
python start_shaftplanner.py --no-browser # do not open the browser
```

The launcher handles: `NO_PROXY` setup (so local requests are not blocked by a
corporate proxy), readiness waiting, unified shutdown on Ctrl+C / process exit,
and idempotent skipping of ports already running.

### Running subsystems individually (debugging)

- `python run_cadagent.py` — cadagent only
- `python frontend/run_frontend.py` — peagent only (auto-starts the backend)
- `cd backend && python run_backend.py` — peagent backend only

### Default addresses

| Service          | URL                          |
|------------------|------------------------------|
| peagent frontend | http://127.0.0.1:8000        |
| peagent backend  | http://127.0.0.1:8001        |
| cadagent         | http://127.0.0.1:8100 (Swagger: `/docs`) |

## 5. Environment variables

Configuration is read from the project-root `.env` file (see `frontend/main.py`,
`frontend/run_frontend.py` and `backend/run_backend.py`):

| Variable              | Default                   | Description                                        |
|-----------------------|---------------------------|----------------------------------------------------|
| `BACKEND_URL`         | `http://127.0.0.1:8001`   | peagent backend base URL (frontend proxy target)   |
| `FRONTEND_URL`        | `http://127.0.0.1:8000`   | peagent frontend URL (launcher / health checks)    |
| `FRONTEND_HOST`       | `127.0.0.1`               | Frontend listen host                               |
| `FRONTEND_PORT`       | `8000`                    | Frontend listen port                               |
| `CAD_AGENT_URL`       | `http://127.0.0.1:8100`   | cadagent service URL (CAD import HTTP target)      |
| `LOG_LEVEL`           | `info`                    | Uvicorn log level                                  |
| `EMBEDDING_API_KEY`   | —                         | Embedding provider API key (required for RAG)      |
| `EMBEDDING_MODEL`     | —                         | Embedding model name (required for RAG)            |
| `NO_PROXY`            | set by the launcher       | Localhost proxy bypass (127.0.0.1, localhost)      |

RAG additionally requires `chromadb` and the embedding configuration above;
the RAG management page (`/rag`) shows a notice when it is unavailable.

## 6. Directory structure

```
ShaftPlanner/
├── backend/          # peagent backend (process planning workflow)
│   ├── app.py / run_backend.py / service.py / repositories.py
│   ├── models/ rules/ agents/ workflow/ providers/ workers/ planners/
│   ├── database/ rag/ tests/
├── frontend/         # peagent frontend (Web UI)
│   ├── main.py / run_frontend.py
│   ├── templates/ static/
├── cadagent/         # 3D CAD analysis (internal package name: cadagent)
│   ├── ui/ services/ config/ core/ agents/ skills/ Scripts/ tests/
├── data/             # capability libraries (machines.xlsx / tools.xlsx) + case base
├── output/           # process card Excel exports
├── scripts/          # tooling scripts (e.g. documentation generation)
├── docs/             # design documents (including the CAD integration design)
├── .env              # unified environment configuration
├── start_shaftplanner.py / run_cadagent.py
└── requirements.txt / environment.yml
```

## 7. Data interpretation

### Machine tool Excel (`data/machines.xlsx`)

Verifies the part's total length against the Turning length, the bar/chuck
diameter capability, and the `Machine production stopped` status.

### Cutting tool Excel (`data/tools.xlsx`)

Verifies material → ISO category, process step, cutting tool grade, First Choice,
coating and applicable materials; it does not cover specific sizes / stock /
tolerance capability, so some operations end up `not_covered` and the overall
result is usually `conditional_pass`, requiring engineer confirmation.

## 8. Tests

```bash
cd cadagent && python -m pytest tests/ -q     # cadagent mapping / validation tests
cd backend && python -m pytest tests/ -q      # peagent backend tests
```
