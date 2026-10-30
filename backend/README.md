# backend

A read-only dashboard backend for ResilientSDN. It discovers completed
experiment runs under `results/`, exposes their metadata over a small HTTP
API, and replays each run's recorded events over a WebSocket so a frontend
can animate utilization and reroutes in simulated real time.

It does not touch `controller/`, Ryu, Mininet, or the evaluation pipeline —
it only reads files already produced under `results/`.

**This is demo-only tooling, not part of the research evaluation.**

## Install

From the project root, using the project's existing `.venv`:

```
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

## Run

From the project root:

```
.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000
```

Then open `http://127.0.0.1:8000/` (serves `frontend/index.html` once the
frontend exists).

## API

- `GET /api/runs` — list of all discovered runs (topology, system, scenario,
  run, target_link, threshold, id).
- `GET /api/runs/{run_id}/meta` — metadata for one run, 404 if unknown.
- `WS /ws/runs/{run_id}/replay?speed=20.0` — replays `events.jsonl` for that
  run as `point`/`reroute` frames paced by `speed`, ending with `done`.
