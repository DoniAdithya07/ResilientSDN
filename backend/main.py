"""Read-only dashboard backend for ResilientSDN.

Serves run metadata and replays recorded events.jsonl files over a
WebSocket for the frontend to visualize. This is demo tooling: it never
touches the controller, Mininet, or the evaluation pipeline, and never
writes into results/ or any other existing project folder.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT_DIR / "results"
FRONTEND_DIR = ROOT_DIR / "frontend"

SPEED_DEFAULT = 20.0
SPEED_MIN = 1.0
SPEED_MAX = 200.0


class RunInfo(BaseModel):
    id: str
    topology: str
    system: str
    scenario: str
    run: int
    target_link: str
    threshold: float


# Fields load_run_info() unconditionally indexes on meta.json. Keep this in
# sync with that function so a run missing any of them is skipped here at
# discovery time instead of crashing list_runs() later.
REQUIRED_META_FIELDS = ("topology", "system", "scenario", "run", "target_link", "threshold")


def discover_runs(results_dir: Path = RESULTS_DIR) -> dict[str, Path]:
    """Scan results/<campaign>/runs/<rundir>/meta.json for replayable runs.

    A run is replayable when its meta.json is valid JSON and has every field
    in REQUIRED_META_FIELDS (older/other campaigns may lack these; skip
    them). The returned id is built only from this directory scan, never
    from client input, so it is safe to use as a lookup key.
    """
    runs: dict[str, Path] = {}
    for meta_path in sorted(results_dir.glob("*/runs/*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if any(field not in meta for field in REQUIRED_META_FIELDS):
            continue
        campaign = meta_path.parent.parent.parent.name
        rundir = meta_path.parent.name
        runs[f"{campaign}__{rundir}"] = meta_path.parent
    return runs


_runs_cache: dict[str, Path] | None = None


def get_runs() -> dict[str, Path]:
    """Lazily discover and cache the id -> run directory mapping."""
    global _runs_cache
    if _runs_cache is None:
        _runs_cache = discover_runs()
    return _runs_cache


def load_run_info(run_id: str, run_dir: Path) -> RunInfo:
    meta = json.loads((run_dir / "meta.json").read_text())
    return RunInfo(
        id=run_id,
        topology=meta["topology"],
        system=meta["system"],
        scenario=meta["scenario"],
        run=meta["run"],
        target_link=meta["target_link"],
        threshold=meta["threshold"],
    )


def resolve_run_dir(run_id: str) -> Path:
    run_dir = get_runs().get(run_id)
    if run_dir is None:
        raise HTTPException(status_code=404, detail="Unknown run id")
    return run_dir


def _read_replay_data(run_dir: Path) -> tuple[str, list[dict]]:
    """Blocking read+parse of a run's meta.json and events.jsonl.

    Run via asyncio.to_thread() so it never blocks the event loop. Raises
    OSError/json.JSONDecodeError on malformed files; callers handle that.
    """
    target_link = json.loads((run_dir / "meta.json").read_text())["target_link"]
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    return target_link, events


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm the run cache before accepting requests.

    Without this, the first request to hit the server could be a WebSocket
    connect, which would otherwise run the full (610-file) discover_runs()
    scan synchronously inside that async handler, blocking every other
    connected client for the duration of the scan.
    """
    runs = get_runs()
    logger.info("Discovered %d runs", len(runs))
    yield


app = FastAPI(title="ResilientSDN Dashboard Backend", lifespan=lifespan)


@app.get("/api/runs")
def list_runs() -> list[RunInfo]:
    return [load_run_info(run_id, run_dir) for run_id, run_dir in get_runs().items()]


@app.get("/api/runs/{run_id}/meta")
def get_run_meta(run_id: str) -> RunInfo:
    return load_run_info(run_id, resolve_run_dir(run_id))


@app.websocket("/ws/runs/{run_id}/replay")
async def replay_run(websocket: WebSocket, run_id: str, speed: float = SPEED_DEFAULT) -> None:
    run_dir = get_runs().get(run_id)
    if run_dir is None:
        await websocket.close(code=4004)
        return

    speed = min(max(speed, SPEED_MIN), SPEED_MAX)
    try:
        target_link, events = await asyncio.to_thread(_read_replay_data, run_dir)
        t0 = events[0]["t"] if events else None
    except (OSError, json.JSONDecodeError, KeyError, IndexError):
        await websocket.close(code=4005)
        return

    await websocket.accept()
    try:
        if events:
            prev_t = t0
            for event in events:
                frame = None
                if event.get("kind") == "stats" and target_link in event.get("util", {}):
                    frame = {"kind": "point", "t": event["t"] - t0, "util": event["util"][target_link]}
                elif event.get("kind") == "reroute":
                    frame = {
                        "kind": "reroute",
                        "t": event["t"] - t0,
                        "flow": event.get("flow"),
                        "reason": event.get("reason"),
                    }

                if frame is None:
                    continue

                await asyncio.sleep(max(0.0, (event["t"] - prev_t) / speed))
                prev_t = event["t"]
                await websocket.send_json(frame)

        await websocket.send_json({"kind": "done"})
        await websocket.close()
    except WebSocketDisconnect:
        pass


# Serve the sibling frontend/ (built in parallel) at "/". The directory may
# not have files yet, so make sure it at least exists.
FRONTEND_DIR.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
