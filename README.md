# ShaftPlanner

[![CI](https://github.com/Taxueovo/ShaftPlanner/actions/workflows/ci.yml/badge.svg)](https://github.com/Taxueovo/ShaftPlanner/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Maintainer](https://img.shields.io/badge/maintainer-Taxueovo-blue)](https://github.com/Taxueovo)

Structured process planning for motor shafts, with one-click launch.

ShaftPlanner plans the machining process route (process planning) of stepped shafts —
turning, keyways, splines, gears, bores, heat treatment and finishing — and verifies
the plan against the local machine tool and cutting tool capability libraries.

Two components:

- **backend** (`backend/`): process planning workflow (input validation, LangGraph,
  capability libraries, process rules, verification, RAG).
- **frontend** (`frontend/`): Jinja2 web UI with dynamic form, status polling, and
  RAG management pages.

## Authors & Maintainers

- **Taxueovo** — core maintainer and primary developer.

## 1. Architecture

```
User
 └─[manual form]──▶ frontend(8000) ──▶ backend(8001) ──▶ process route + resource verification
```

- `backend/`: input validation, LangGraph workflow, machine tool / cutting tool
  Excel libraries, process rules, verification, RAG.
- `frontend/`: Jinja2 pages, dynamic form, status polling, HTTP proxy.
- The frontend never imports backend business functions; the two processes
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

## 4. Running

### One-click start (recommended)

```bash
python start_shaftplanner.py              # starts the backend + frontend, opens the browser
python start_shaftplanner.py --no-browser # do not open the browser
```

The launcher handles: `NO_PROXY` setup (so local requests are not blocked by a
corporate proxy), readiness waiting, unified shutdown on Ctrl+C / process exit,
and idempotent skipping of ports already running.

### Running subsystems individually (debugging)

- `python frontend/run_frontend.py` — frontend only (auto-starts the backend)
- `cd backend && python run_backend.py` — backend only

### Default addresses

| Service          | URL                   |
|------------------|-----------------------|
| peagent frontend | http://127.0.0.1:8000 |
| peagent backend  | http://127.0.0.1:8001 |

## 5. Environment variables

Configuration is read from the project-root `.env` file (see `frontend/main.py`,
`frontend/run_frontend.py` and `backend/run_backend.py`):

| Variable              | Default                   | Description                                        |
|-----------------------|---------------------------|----------------------------------------------------|
| `BACKEND_URL`         | `http://127.0.0.1:8001`   | peagent backend base URL (frontend proxy target)   |
| `FRONTEND_URL`        | `http://127.0.0.1:8000`   | peagent frontend URL (launcher / health checks)    |
| `FRONTEND_HOST`       | `127.0.0.1`               | Frontend listen host                               |
| `FRONTEND_PORT`       | `8000`                    | Frontend listen port                               |
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
├── data/             # capability libraries (machines.xlsx / tools.xlsx) + case base
├── output/           # process card Excel exports
├── scripts/          # tooling scripts (e.g. documentation generation)
├── docs/             # design documents
├── .env              # unified environment configuration
├── start_shaftplanner.py
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
cd backend && python -m pytest tests/ -q      # peagent backend tests
```
