# Release check config: ResilientSDN

## Project rules (breaking any is a hard FAIL)
- All work stays inside the project folder; scratch output goes to `.local\` (user's global rule).
- Never commit or push; the user pushes.
- Experiments run only inside the Docker container (needs root, Mininet, OVS).

## Environment
- Virtual env / toolchain: `.venv\` (Python 3.11, Windows) for tests and analysis;
  Docker image `resilient-sdn:latest` (Python 3.11 + Mininet, Python 3.9 + Ryu at `/opt/ryu`).
- Required env vars: none on Windows. Container: `PYTHONPATH=/project` (set in docker/compose.yaml).
- Docker Desktop must be running; `docker/sdn.ps1` loads the `openvswitch` kernel module.

## Commands
- Install: `python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt`; image: `.\docker\sdn.ps1 build`
- Lint: `.venv\Scripts\python -m ruff check .` (ruff installed into `.venv` as a dev tool)
- Syntax check (Ryu code cannot be imported on Windows): `.venv\Scripts\python -m py_compile` on every module
- Tests (quick): `.venv\Scripts\python -m pytest`
- Tests (full): quick + `.\docker\sdn.ps1 sh docker/check_env.sh` + `docker/connectivity_check.sh` for fat_tree / internet2 / geant
  + `python3 -m evaluation.experiment --config config/smoke_config.yaml` in the container
- Build / package: `docker compose build`
- Output file: none (research code; results in `results/<name>/`)

## Baselines (fill after first approved full run)
| Measure | Command | Baseline | Fail if |
|---|---|---|---|
| Unit tests passing | pytest | | fewer than baseline |
| Fat-tree pingall | connectivity_check.sh | | any loss |

## Smoke test
1. Container starts, `ryu-manager --version` prints 4.34 → OK
2. Ryu + each topology, pingall → 0% dropped
3. Smoke experiment → every run writes a CSV row, no Traceback in any `ryu.log`

## Human-only checks
- Full 30-run campaign (≈4.5 days) and checking the figures make sense for the paper.
