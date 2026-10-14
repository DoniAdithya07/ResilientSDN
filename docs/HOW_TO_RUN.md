# How to run ResilientSDN: full process and demo

Author: Doni Adithya. Everything runs on one Windows laptop: the network emulator and controller run in
WSL2 Ubuntu 24.04, and the analysis and PDF reports run in the Windows Python environment (`.venv`).
Docker Desktop is **not** used; keep it closed while running (it shares the Linux kernel with WSL).

## 1. Before you start
- Laptop plugged in, "when I close the lid" set to "do nothing", Windows Update paused.
- Open **Ubuntu 24.04** from the Start menu (or run `wsl -d Ubuntu-24.04` in PowerShell), then:
  `cd "/mnt/d/sdn project  and assingmensts/project"`
- Optional tool check (1 minute): `./run.sh sh docker/check_env.sh`
- Unit tests (Windows PowerShell, project folder): `.venv\Scripts\python -m pytest -q` (127 tests pass).

## 2. Live demo version (about 6 minutes)
```
./demo.sh
```
It runs fat-tree with no attack (S1) and one attack (S3) for all 3 systems (Static, Simple reactive,
ResilientSDN), then builds `results/demo/report.pdf` automatically.

**How to know the demo worked properly and accurately:** open `results/demo/report.pdf`. The first page has a
*Correctness checks* table computed from the run itself. Every line must say **PASS**:

| Check | What it proves |
|---|---|
| No run failed | emulator, controller and traffic all started and finished |
| Static routing never reroutes | the baseline really does nothing |
| No attack (S1): no reroute, no congestion | no false alarms |
| Measured target load matches theory (S3 about 90%) | the emulator delivers the attack the scenario asks for (accuracy) |
| Legit throughput near 10 Mbps when loss is small | traffic generation and measurement agree |

What you should see in the demo: static stays congested, simple reactive flaps (tens of reroutes),
ResilientSDN makes only a few reroutes and settles. A demo proves the system works; it does **not** prove the
performance claims. Those need the full process below.
Do not run the demo while the full campaign is running.

## 3. Full process (about 17 to 19 hours)
```
./run_full.sh
```
| Part | Runs | Time |
|---|---|---|
| Fat-tree, 3 systems x S1-S11 x 5 runs | 165 | about 6 h |
| NSFNET, Internet2, GEANT x S1, S3, S5, S9, S11 x 5 runs | 225 | about 7 h |
| Ablation A-D on fat-tree, 10 runs per case | 220 | about 6 h |

**Why so long:** every run plays out in real time (3 s warm-up, 10 s normal traffic, 60 s attack, cleanup; S11
120 s, S6 600 s), about 1.5 minutes each. 610 runs are needed for meaningful confidence intervals, and runs
cannot overlap (one switch service, one controller port, one cleanup step).

**If it stops** (sleep, restart, power cut): run `./run_full.sh` again. Finished runs are skipped; only the run in
progress is repeated. Progress: `results/fattree_5runs/campaign.log`, `results/wan_5runs/campaign.log`.
Wi-Fi is not needed.

## 3b. Use any other topology (no code changes)
Any network from the Internet Topology Zoo, or your own GraphML file, works:
```
.venv\Scripts\python -m topology.add Abilene          # Windows PowerShell: downloads and checks it
./run.sh sh docker/connectivity_check.sh abilene        # WSL: controller + ping all hosts
./run.sh python3 -m evaluation.experiment --out results/abilene --topologies abilene --runs 5
```
The name is the file name in lower case (`abilene`). Zoo names: github.com/mroughan/InternetTopologyZoo/tree/main/graphml.
The loader repairs messy files (parallel links, missing names, missing coordinates or speeds, disconnected
pieces) and sets the controller's path-search limit from the topology. Tested: Abilene, Arnes, Renater2010.
Each results folder gets a `report.pdf`; the final combined PDF covers every results folder:
`.venv\Scripts\python -W ignore -m analysis.final_report` (a new file each time, never overwrites; `run_full.sh` does it at the end).

## 4. After a run: reports and analysis (Windows PowerShell, project folder)
Each part builds its own `report.pdf` at the end (tables, figures, explanation of why the results came out that
way, correctness checks, limits). To rebuild or run the other tools:
```
.venv\Scripts\python -m analysis.run_report results\fattree_5runs       # PDF for one results folder
.venv\Scripts\python -m evaluation.stats results\fattree_5runs\all_results.csv   # summary.csv (mean, 95% CI, Welch t-test)
.venv\Scripts\python -m analysis.plot_results results\fattree_5runs     # paper figures
.venv\Scripts\python reports\make_report.py                             # the full project report PDF
```
After a change to `evaluation/metrics.py`, recompute the old results without re-running:
`.venv\Scripts\python -m evaluation.recompute results\<folder>`

## 5. Single commands
```
./run.sh sh docker/connectivity_check.sh nsfnet     # controller + ping all hosts (fat_tree, internet2, geant, nsfnet)
./run.sh python3 -m evaluation.experiment --out results/x --topologies nsfnet --scenarios S3 --runs 1
./run.sh python3 -m evaluation.ablation A --topology fat_tree --runs 5
```
Topologies: `fat_tree`, `internet2`, `geant`, `nsfnet`. Scenarios: S1-S11 (`attack/scenarios.py`).
Systems: `static`, `simple_reactive`, `resilient_sdn`.
