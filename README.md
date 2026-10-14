# ResilientSDN

Congestion-attack-aware rerouting for SDN, compared with static routing and a simple reactive controller, on
four topologies (fat-tree, Internet2, GEANT, NSFNET) and eleven attack scenarios, using Mininet, Open vSwitch and Ryu.

**Run it:** see [docs/HOW_TO_RUN.md](docs/HOW_TO_RUN.md): a 6-minute live demo (`./demo.sh`) and the full
17-19 hour evaluation (`./run_full.sh`).

## Folder map
| Folder / file | What it holds |
|---|---|
| `controller/` | ResilientSDN: monitor, detector, path engine, scorer, hysteresis, flow manager, main app |
| `baselines/` | Static routing, simple reactive, QoSentry interface |
| `attack/`, `traffic/` | Attack scenarios S1-S11, legitimate traffic, trace replay |
| `topology/` | Fat-tree, Internet2, GEANT, NSFNET (`data/` has the source files and `SOURCES.md`) |
| `evaluation/` | Experiment runner, metrics, ablation, statistics, recompute |
| `analysis/` | Figures, LaTeX tables, `run_report.py` (PDF report with correctness checks) |
| `config/` | Controller, network and experiment settings |
| `tests/` | 127 unit tests (`.venv\Scripts\python -m pytest -q`) |
| `docker/` | Environment check and connectivity scripts (also used in WSL); optional Docker image (`Dockerfile`, `compose.yaml`, `sdn.ps1`) |
| `results/` | Output of every run: CSV, per-run logs, `report.pdf` |
| `docs/` | How-to-run guide, implementation plan, progress log (`PROGRESS.md`), private notes |
| `demo.sh`, `run_full.sh`, `run.sh` | Demo, full evaluation, and the root wrapper for Mininet commands |
