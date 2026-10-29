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
| `tools/` | `live_view.py`: watch a demo or experiment live in the browser (see below) |
| `config/` | Controller, network and experiment settings |
| `tests/` | 127 unit tests (`.venv\Scripts\python -m pytest -q`) |
| `docker/` | Environment check and connectivity scripts (also used in WSL); optional Docker image (`Dockerfile`, `compose.yaml`, `sdn.ps1`) |
| `results/` | Output of every run: CSV, per-run logs, `report.pdf` |
| `docs/` | How-to-run guide, implementation plan, progress log (`PROGRESS.md`), private notes |
| `demo.sh`, `run_full.sh`, `run.sh` | Demo, full evaluation, and the root wrapper for Mininet commands |

## Live view in the browser
Start it with no arguments; this is the one way in:

```sh
python3 tools/live_view.py        # asks for the sudo password once, then opens http://127.0.0.1:8765/
```

The page has three tabs, a status chip (Ready, Running with "run 3 of 6 · 2:14 left", Password needed,
Finished, Stopped, Error) and a Stop button that are always visible:

- **Run a test**: *Quick demo* (fat-tree, 3 systems with and without an attack, 6 runs, about 6–8 min);
  *Test a topology* (the same 6 runs on any network in the list, results in `results/<name>_demo`; a network
  from the [Topology Zoo](https://topology-zoo.org) can be added by name, e.g. `Abilene`); *Complete
  evaluation* (`./run_full.sh`, 17–19 h, asks to confirm).
- **Watch live**: one plain sentence about what is happening, the run's phases, four numbers (busiest link,
  seconds over the 0.8 limit, reroutes, dropped packets), link use over time and a network map (fat-tree in
  layers, real backbones drawn from their cities' coordinates). *Show details* opens the controller actions
  per run (click a row to replay that run), the terminal output, controller events, `ryu.log` and run files.
- **Results**: a one-sentence conclusion and the three systems side by side (time over the limit, reroutes,
  path flip-flops, packet loss, throughput) for the test with and without an attack, also as a table.

The page switches to Watch live when a test starts and to Results when it finishes (not if you picked a tab
in the last 20 s). It can only start these allow-listed commands; the page's requests need a random
per-run token, its own Origin and Host and JSON, and nothing typed in the page is passed to a shell.

Older ways still work and show the same page, with the Start buttons disabled:

```sh
python3 tools/live_view.py ./demo.sh                     # run a command and watch it
python3 tools/live_view.py -- ./run.sh python3 -m evaluation.experiment --out results/x --topologies fat_tree --runs 1
python3 tools/live_view.py --out results/demo            # watch only (a run started in another terminal)
```

Options: `--port N` (default 8765, `0` = any free port), `--out DIR` (results folder; default from the
command's `--out`, `results/demo` for `demo.sh`, else `results`), `--no-browser`. sudo asks for the password
in the terminal. The page is served on 127.0.0.1 only. Ctrl+C stops the command; after the command ends the
page stays up until Ctrl+C. The exit code is the command's. Standard library only (the topology list and
maps use the project's `topology` package).
