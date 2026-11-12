# Reproducing every result in the paper

This file gives the exact steps to install the environment, run the experiments, and regenerate every
number, table and figure of the paper *"ResilientSDN: Capacity-Verified, Hysteresis-Damped Rerouting
Against Link-Flooding Attacks in Software-Defined Networks"*.

All results in the paper were produced exactly this way on one laptop (Intel Core i7-1255U, 8 GB RAM,
Windows 11 Home, WSL2 Ubuntu 26.04.1 LTS, kernel 6.6.87) on 9-10 November 2026.

---

## 1. Requirements

| Item | Version used | Notes |
|---|---|---|
| OS | Ubuntu 26.04.1 LTS in WSL2 (Windows 11); native Ubuntu works too | Mininet and Open vSwitch need a Linux kernel |
| Mininet | 2.3.0 | apt package `mininet` |
| Open vSwitch | 3.7.1 | apt package `openvswitch-switch` |
| iperf3 | 3.20 | traffic generation |
| Python (runner, metrics, analysis) | 3.14 (system) | networkx 3.2.1, pandas 2.3.3, scipy 1.16.3, matplotlib 3.10.7, numpy 2.3.5, PyYAML 6.0.3, reportlab 4.4.10, pypdf 6.9.2 |
| Python (controller) | 3.9.25 in `/opt/ryu` | Ryu 4.34, eventlet 0.30.2 (Ryu does not run on Python >= 3.10) |
| LaTeX | TeX Live 2025 | only to rebuild the paper PDF (IEEEtran class) |
| Disk / RAM | about 3 GB free, 8 GB RAM | |

`requirements.txt` lists the Python analysis packages, `requirements-ryu.txt` the controller packages.

## 2. Install (once)

**Windows only, in PowerShell:** install WSL and Ubuntu, then open it.
```powershell
wsl --install -d Ubuntu
wsl
```

**In Ubuntu:**
```sh
git clone https://github.com/DoniAdithya07/ResilientSDN.git ~/ResilientSDN   # or unzip the submitted code
cd ~/ResilientSDN
sudo sh setup_wsl.sh                 # apt packages + Ryu 4.34 in a Python 3.9 venv at /opt/ryu (~10 min)
./run.sh sh docker/check_env.sh      # must print tool versions, "OK", and "Results: 0% dropped"
python3 -m pytest -q                 # unit tests (218 pass)
```
Keep Docker Desktop closed while running experiments (it shares the WSL kernel), keep the laptop on
AC power and stop it from sleeping.

## 3. Quick check: the 6-minute demo

```sh
./demo.sh                            # fat-tree, S1 and S3, 3 systems, 1 run each
```
Open `results/demo/report.pdf`; every line of the *Correctness checks* table must say PASS.

## 4. Run the full evaluation (about 17-19 hours)

```sh
./run_full.sh                        # asks for the sudo password once
```
It runs, one after another (this is the content of `run_full.sh`):
```sh
./run.sh python3 -m evaluation.experiment --out results/fattree_5runs --topologies fat_tree --runs 5
./run.sh python3 -m evaluation.experiment --out results/wan_5runs --topologies nsfnet internet2 geant \
    --scenarios S1 S3 S5 S9 S11 --runs 5
./run.sh python3 -m evaluation.ablation A B C D --topology fat_tree --runs 10
```

| Part | Runs | Time |
|---|---|---|
| Fat-tree: 3 systems x 11 scenarios (S1-S11) x 5 runs | 165 | ~6 h |
| NSFNET, Internet2, GEANT x {S1, S3, S5, S9, S11} x 3 systems x 5 runs | 225 | ~7 h |
| Ablation A-D on the fat-tree, 10 runs per variant and scenario | 220 | ~6 h |

The runner is **resumable**: if it stops (sleep, reboot), run `./run_full.sh` again and finished runs are
skipped. A failing run writes `runs/<run>/error.txt`, gets no CSV row, and is retried on the next start.

### 4b. Revision-1 experiments (added after Google Deep Review round 1, about 8 hours)

```sh
./run_r1.sh                          # resumable, like run_full.sh
```
It runs (this is the content of `run_r1.sh`):
```sh
./run.sh python3 -m evaluation.experiment --out results/r1_wan_variants --topologies nsfnet internet2 geant \
    --systems rsdn_shortest rsdn_norm_drift rsdn_relief_bypass --scenarios S3 S5 S9 S11 --runs 5
./run.sh python3 -m evaluation.experiment --out results/r1_capacity_loo --topologies fat_tree \
    --systems rsdn_no_capacity_filter resilient_sdn --scenarios S3 S5 S11 --runs 10
./run.sh python3 -m evaluation.experiment --out results/r1_ecmp --topologies fat_tree --systems static_ecmp --runs 5
./run.sh python3 -m evaluation.experiment --out results/r1_relief_fat --topologies fat_tree \
    --systems rsdn_relief_bypass --scenarios S3 S8 S11 --runs 5
```

| Part | What it tests | Runs |
|---|---|---|
| `r1_wan_variants` | score ablation on the backbones (`rsdn_shortest`); two candidate fixes for the long-detour hold: path-length-normalised drift (`rsdn_norm_drift`) and relief bypass (`rsdn_relief_bypass`) | 180 |
| `r1_capacity_loo` | leave-one-out ablation A': ResilientSDN without only the capacity filter, against full ResilientSDN | 60 |
| `r1_ecmp` | static ECMP baseline (`baselines/ecmp_static.py`) on the fat-tree, all scenarios | 55 |
| `r1_relief_fat` | relief bypass on the fat-tree (cost of the fix where detours are short) | 15 |

The same seeds are used as in section 4, so every revision run is paired with a run of the original campaign.
Variant definitions: `VARIANTS` in `evaluation/experiment.py`.

### 4c. Revision-2 experiments (added after Google Deep Review round 2, about 6-7 hours)

```sh
./run_r2.sh                          # resumable, like run_full.sh
```
It runs (this is the content of `run_r2.sh`):
```sh
./run.sh python3 -m evaluation.experiment --out results/r2_ecmp_attacker --attacker ecmp --topologies fat_tree \
    --systems static static_ecmp resilient_sdn --scenarios S3 S4 S5 S11 --runs 5
./run.sh python3 -m evaluation.experiment --out results/r2_gate_all --topologies nsfnet internet2 geant \
    --systems rsdn_gate_all --scenarios S3 S5 S9 S11 --runs 5
./run.sh python3 -m evaluation.experiment --out results/r2_capacity_loo_wan --topologies nsfnet internet2 geant \
    --systems rsdn_no_capacity_filter --scenarios S3 S5 S9 S11 --runs 5
./run.sh python3 -m evaluation.experiment --out results/r2_sequential --topologies fat_tree \
    --systems rsdn_sequential_only --scenarios S3 S4 S5 S11 --runs 10
```

| Part | What it tests | Runs |
|---|---|---|
| `r2_ecmp_attacker` | attacker that maps the ECMP routes (`--attacker ecmp`: legitimate and attack pairs cross the target under static AND ECMP routing, background pairs under neither); static, static ECMP and ResilientSDN on the fat-tree | 60 |
| `r2_gate_all` | DPP deadband applied to every candidate before the score (`rsdn_gate_all`, option `dpp_gate_all_candidates`) | 60 |
| `r2_capacity_loo_wan` | leave-one-out without the capacity filter on the backbones, including the presaturated S9 | 60 |
| `r2_sequential` | ablation A'': one-at-a-time moves with projected loads only (`rsdn_sequential_only`), same seeds as ablation A | 40 |

`--attacker ecmp` changes the traffic plan (it is recorded as `"attacker": "ecmp_aware"` in each run's
`meta.json`), so `r2_ecmp_attacker` contains its own static and ResilientSDN runs instead of reusing section 4.

**Do not wrap the command in a shell whose command line contains the words `ryu-manager`, `iperf3`,
`tcpreplay` or `ping -i`**: the runner's cleanup step kills processes matching those words.

## 5. Regenerate the paper's numbers, tables, figures and PDF

```sh
python3 paper/make_assets.py         # reads results/*, writes paper/figures/*.pdf and paper/generated/*.tex
cd paper && latexmk -pdf main.tex    # builds paper/main.pdf
```
`make_assets.py` computes every number quoted in the text (`paper/generated/numbers.tex`), the result
tables (`tables.tex`, `fidelity.tex`) and all figures directly from the raw run files, so the paper always
matches the data. A value that cannot be computed yet appears in red as `??` instead of silently wrong.

| Paper item | Produced by | From |
|---|---|---|
| Abstract and text numbers | `paper/make_assets.py` → `generated/numbers.tex` | all `results/*/all_results.csv` + `events.jsonl` |
| Table: fidelity (planned vs measured load) | `make_assets.py` → `generated/fidelity.tex` | `events.jsonl`, `meta.json` |
| Table: fat-tree results | `make_assets.py` → `generated/tables.tex` | `results/fattree_5runs` |
| Table: WAN results | `make_assets.py` → `generated/tables.tex` | `results/wan_5runs` |
| Table: ablation | `make_assets.py` → `generated/tables.tex` | `results/ablation_{A,B,C,D}` |
| Fig. timeline fat-tree S3 | `make_assets.py` → `figures/timeline_fat_s3.pdf` | `results/fattree_5runs/runs/fat_tree_*_S3_0` |
| Fig. fat-tree scenarios | `make_assets.py` → `figures/fat_scenarios.pdf` | `results/fattree_5runs` |
| Fig. WAN | `make_assets.py` → `figures/wan.pdf` | `results/wan_5runs` |
| Fig. ablation | `make_assets.py` → `figures/ablation.pdf` | `results/ablation_*` |
| Fig. Internet2 S11 timeline | `make_assets.py` → `figures/timeline_inet_s11.pdf` | `results/wan_5runs/runs/internet2_*_S11_0` |
| Table VIII (revision experiments on the backbones) and Sec. VI-I numbers | `make_assets.py` → `generated/tables_r1.tex`, `numbers.tex` | `results/r1_wan_variants`, `results/r2_gate_all`, `results/r2_capacity_loo_wan`, paired with `results/wan_5runs` |
| Table VII (legitimate throughput and ping RTT) | `make_assets.py` → `generated/table_legit.tex` | `results/fattree_5runs`, `results/wan_5runs` |
| ECMP-aware attacker (Sec. VI-I) | `make_assets.py` → `numbers.tex` (`Ea*`) | `results/r2_ecmp_attacker` |
| Ablation A'' (Table VI, Fig. 4) | `make_assets.py` | `results/r2_sequential`, paired with `results/ablation_A` |
| Static ECMP rows in Table IV | `make_assets.py` → `generated/tables.tex` | `results/r1_ecmp` |
| Ablation A' (Table VI, Fig. 4) | `make_assets.py` | `results/r1_capacity_loo` |
| Held-flow detour lengths (Sec. VII) | `make_assets.py` (`held_detours`) | `results/wan_5runs/runs/*/events.jsonl`, `meta.json` |
| Reference order | `python3 paper/order_bib.py` | `paper/main.tex` |
| Revision report | `paper/revision/make_revision_report.py` | `paper/revision/rounds.json`, `round1_comments.json`, `round2_comments.json`, `numbers_r1.tex`, `reviews/` |
| Tables: topologies, scenarios | written in `main.tex` | `config/network_config.yaml`, `attack/scenarios.py` |

Other analysis tools (optional): `python3 -m evaluation.stats results/<part>/all_results.csv` (mean, 95% CI,
Welch t-test → `summary.csv`), `python3 -m analysis.run_report results/<part>` (a PDF report per folder,
built automatically after each part), `python3 -m evaluation.recompute results/<part>` (recompute metrics
from saved run files after a change to `evaluation/metrics.py`).

## 6. Parameters, seeds and settings

| Setting | Value | Where |
|---|---|---|
| Polling interval Δ | 2 s | `config/controller_config.yaml` |
| Threshold θ / safe threshold θs | 0.80 / 0.80 | same |
| Consecutive polls N / repeat window W | 3 / 120 s | same |
| Cooldown Tc / deadband ε / V0 | 30 s / 0.05 / 1.0 | same |
| Score weights w1, w2, w3 | 0.40, 0.30, 0.30 | same |
| Max path hops H | 6 (Internet2: 12; added Zoo topologies: detour + 2, in [6, 12]) | same + `config/network_config.yaml` |
| S10 randomisation | N ~ U{2..5}, Tc ~ U[20, 45] s | `controller_config.yaml` → `adversarial` |
| Ablation switches | capacity_filter, hysteresis, scoring, trend_trigger; revision 1: `controller.dpp_normalise_by_length`, `controller.relief_bypass` | `controller_config.yaml`; variants in `evaluation/ablation.py` and `VARIANTS` in `evaluation/experiment.py` |
| Run phases | warm-up 3 s, legitimate only 10 s, attack 60 s (S11 120 s, S6 600 s), +3 s | `config/experiment_config.yaml` |
| Legitimate traffic | 2 flows x 10 Mbit/s across the target, 4 background flows x 5 Mbit/s | same |
| Bursty traffic (ablation D) | 35 Mbit/s per legitimate pair, 3 s every 12 s | same |
| Runs per case | 5 (ablation 10) | `run_full.sh` |
| **Random seed** | the string `"<topology>-<scenario>-<run>"` (e.g. `fat_tree-S3-0`) seeds host-pair selection, the S10 pattern choice, and the controller's S10 randomisation; identical for all systems in the same run | `evaluation/experiment.py` (`run_single`) |
| Controller config of every run | saved as `runs/<run>/controller_config.yaml` | written by the runner |

Baseline configurations: **static** (`baselines/static_routing.py`) = minimum-latency path, never moved; **static ECMP** (`baselines/ecmp_static.py`, revision 1) = one of the equal-cost minimum-latency paths chosen by a CRC32 hash of the flow (source and destination address), never moved;
**simple reactive** (`baselines/simple_reactive.py`) = any link > θ → every flow on it to the
minimum-latency path avoiding that link, single-poll trigger, no capacity check, no hysteresis.
Both use the same base controller (`controller/routing_base.py`) as ResilientSDN.

## 7. Data

* **Topologies** (in `topology/data/`, sources in `topology/data/SOURCES.md`): Internet2 OS3E
  (Heller et al., HotSDN 2012), GEANT 2012 and NSFNET (Internet Topology Zoo, CC BY 4.0). Included in the
  code; no download needed. Further Zoo topologies: `python3 -m topology.add <Name>`.
* **Traffic is synthetic.** All legitimate, background and attack traffic is generated by iperf3 (UDP) at
  the rates and timings in `config/experiment_config.yaml` and `attack/scenarios.py`. No packet trace was
  used (trace replay is supported via `trace.file`, but it is `null` in all reported experiments).
* **Raw results**: each run leaves `results/<part>/runs/<topology>_<system>_<scenario>_<run>/` with
  `events.jsonl` (controller log), iperf3 JSON, ping output, `meta.json` and `ryu.log`; one row per run in
  `results/<part>/all_results.csv`. Metric definitions: `evaluation/metrics.py`.

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `sudo` asks for a password every run | normal; or run inside `sudo -s` |
| `nothing listening on port 6653` | another controller is running: `sudo pkill -f ryu-manager; sudo mn -c` |
| a run failed | see `results/<part>/runs/<run>/error.txt`; re-run the same command (only failed runs repeat) |
| WSL stopped overnight | Windows went to sleep; set "sleep: never" while plugged in, then re-run `./run_full.sh` |
