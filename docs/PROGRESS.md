# ResilientSDN — Progress Tracker

Full plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

**Status legend:** ⬜ Not started · 🟨 In progress · ✅ Done · ⛔ Blocked

**Last updated:** 2026-09-29 01:55

---

## Right Now (2026-10-01, evaluation finished; weak points investigated, not fixed)

| Item | Status |
|------|--------|
| Fat-tree (165 runs), WAN (225), ablation A-D (220) | ✅ all finished, 0 failed; each folder has a report.pdf |
| Final PDF | ✅ reports/ResilientSDN_Final_Results.pdf (30 pages; new file, existing reports untouched) - rebuild with analysis.final_report only under a new --out name |
| Final QA | ✅ PASS (127 tests, demo.sh ran, correctness table all PASS, CSV integrity) |
| Skeptical data review | ✅ done; wording in the final PDF made defensible (medians, paired sign tests, no pooled ablation p-values, disclosure of the post-hoc metric) |
| Cleanup | ✅ .local scratch and test result folders removed; results/*/all_results.before_recompute.csv kept on purpose (shows the metric change) |
| Demo | ./demo.sh in WSL (~6 min), see docs/HOW_TO_RUN.md |
| Latest PDF | ✅ reports/ResilientSDN_Final_Results_v2.pdf (30 pages; adds the failed-fix note and the S7 no-harm numbers). The earlier PDFs are untouched; the generator numbers new files (_v2, _v3 ...) and never overwrites |
| Any topology | ✅ `python -m topology.add <ZooName>` then use the lower-case name everywhere; live-tested on Abilene (110/110 pings), Arnes (1122/1122), Renater2010 (1806/1806); short Abilene experiment 0 failures |
| Weak points | ⚠️ investigated, **not fixed** (see "Weak points: status" below); the original ResilientSDN stays the reference |
| Tests | ✅ 130 pass |

## Weak points: status (2026-10-01)

| Weak point | Fixed? | Finding |
|---|---|---|
| Internet2 S11: target link stays above threshold | No | Cause: DPP drift sums utilisation over a path, so 7-13 hop WAN detours always score worse than staying put and the move is held (172 and 114 held decisions; fat-tree and GEANT: 0). Legitimate flows still move, so loss is low (0.6% against 9.4% for static). |
| Internet2 S9: no relief | No, not a bug | The attacker fills the detours to 79%; the capacity filter correctly refuses to move traffic onto them. |
| Pulse attack S7 | Nothing to fix | 0 loss, latency about 12.7 ms and 10 Mbps for all three systems; ResilientSDN correctly does nothing, simple reactive makes 48 pointless reroutes. |
| NSFNET S9: loss equal to static | Not diagnosed | Static and ResilientSDN lose the same amount run by run, so it follows the seed or traffic, not the controller. |
| Few runs per case (5; ablation 10) | No | More runs cost many more hours. |

Two fix attempts (relief bypass; bypass plus a 75% safe limit) were rejected, details in the next section. Options left: (1) keep the results as they are and state the limits (recommended); (2) try another fix, e.g. choose the flow to move by rate, which changes the algorithm and needs about 3-5 h of re-runs with no guarantee; (3) add runs to tighten the statistics.

## Added 2026-09-29: NSFNET topology + automatic PDF report
- **NSFNET** (13 nodes / 15 links) downloaded from the Internet Topology Zoo (`topology/data/Nsfnet.graphml`, CC BY 4.0), loaded by the new generic `topology/zoo.py` (GÉANT now uses it too). Live test in WSL: pingall 156/156; S1/S3/S5 × 3 systems ran with 0 failures (`results/nsfnet_test`). Not in the default 30-run campaign: add `nsfnet` to `topologies` in `config/experiment_config.yaml` to include it.
- **`analysis/run_report.py`** builds `<results>/report.pdf` (tables, figures, per-scenario "why it came out this way", target-link timeline). `evaluation/experiment.py` calls it at the end of every run; in WSL it uses the Windows `.venv`, in the container it prints the command. Manual: `.venv\Scripts\python -m analysis.run_report results\<name>`.
- **The fat-tree campaign is stopped at 79/150** (Docker Desktop was not running). Re-run the command below to resume; finished runs are skipped.

## Tried and rejected, 2026-10-01 (attempt to fix the Internet2 weak spot)
- **Root cause found (evidence: events.jsonl of Internet2 S11 runs):** the DPP drift sums utilisation over every link of a path, and Internet2 detours have 7-13 hops, so every detour scores worse than staying on the congested path (172 and 114 held decisions in two runs; fat-tree and GEANT S11: 0). Legitimate flows still move (loss 0.6% against 9.4% for static); the attack flow stays, so the link stays above the threshold. In S9 the detours are pre-filled to 79% and the capacity filter correctly refuses them: by design, not a bug.
- **Attempt 1, relief bypass** (`rsdn_relief_bypass` in `evaluation/experiment.py`: no DPP deadband for a flow leaving a congested link, cooldown kept): Internet2 S11 time above threshold 96 s to 13 s, but S3 worse (8 s to 22 s, secondary congestion 1.6 to 9): the 70 Mbps attack flow was moved onto a detour at 82% (capacity filter limit equals the 80% trigger).
- **Attempt 2, bypass plus a 75% safe limit:** S11 kept its gain, but S3 now lost traffic (2.4% on average, 4.6% in one run; original: 0%). Worse.
- **Conclusion:** moving the large attack flow around a sparse WAN relieves the target link but causes collateral loss. The original hold protects the legitimate flows. Not adopted; the original ResilientSDN stays the reference. (2 runs per test only; small samples.)
- **S7 (pulse attack) is not a real weakness:** all three systems have 0 loss, latency about 12.7 ms and 10 Mbps throughput; ResilientSDN correctly does nothing while simple reactive makes 48 pointless reroutes.

## Next Steps (in order)
1. Decide what to do about the weak points: keep as is (recommended), try another algorithm fix, or add runs (see "Weak points: status").
2. Hand over for the push: delete nothing else, check `git status`, commit and push yourself (never done by Claude).
3. Open decisions from the plan: attack-flow classification, ECMP static baseline, QoSentry model, CAIDA/MAWI traces.
4. Optional: remove the Docker files (`docker/Dockerfile`, `compose.yaml`, `sdn.ps1`, `entrypoint.sh`); the WSL route is the one in use.
(Done since the earlier list: campaign finished, metrics fixed post-hoc, Internet2 hop limit raised to 12, final PDFs built, WSL tested.)

---

## Overall Status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Environment setup | ✅ Windows `.venv`; Docker container (Mininet/OVS/Ryu); WSL2 Ubuntu 24.04 + `run.sh` |
| 2 | Network topology | ✅ Fat-tree, Internet2 (OS3E), GÉANT |
| 3 | Monitoring module | ✅ port/flow stats, utilisation, probe latency — verified live |
| 4 | Congestion detector | ✅ |
| 5 | Path evaluation engine | ✅ (DPP changed to drift form — see Changes) |
| 6 | Flow rule manager | ✅ |
| 7 | Main controller | ✅ reroutes live on Fat-tree (S3 recovers in ~8 s) |
| 8 | Baselines | ✅ static, simple reactive; QoSentry = interface only (needs a trained policy) |
| 9 | Attack simulation | ✅ S1–S10 verified live (pulse / timed / presaturation shapes checked) |
| 10 | Evaluation and experiment automation | 🟨 runner (resumable, crash-safe), metrics, ablation A–E, Pareto, stats done; **Fat-tree 5-run campaign 76/150**; 30-run campaign and ablation not run yet |
| 11 | Results and visualisation | 🟨 figures, LaTeX tables and the final PDF report built from the campaign so far; final numbers after the campaign |

**Tests:** 112 unit tests passing · release check (full) passed after fixes · `/team` check PASS WITH ISSUES (see Open Issues)

---

## How to run

**Unit tests (Windows):**
```powershell
.venv\Scripts\python -m pytest
```

**WSL2 Ubuntu (main way to run and demo the project):** Ubuntu 24.04 installed at `D:\DevTools\wsl\Ubuntu-24.04`, user `donia` (passwordless sudo). Same tools as the container: Mininet 2.3.0, Open vSwitch 3.3.9, iperf3, tcpreplay, tshark, hping3; Ryu 4.34 in a Python 3.9 venv at `/opt/ryu`. Open vSwitch starts only when `run.sh` is used. Open **Ubuntu 24.04** from the Start menu, then:
```bash
cd "/mnt/d/sdn project  and assingmensts/project"
./run.sh sh docker/check_env.sh                     # tools + mn --test pingall
./run.sh sh docker/connectivity_check.sh fat_tree   # Ryu + pingall (fat_tree / internet2 / geant)
./run.sh python3 -m evaluation.experiment --out results/x --topologies fat_tree --runs 5
```
Don't run Mininet in WSL and in the Docker container at the same time (they share one Linux kernel; each run's cleanup would disturb the other).

**Container (Docker Desktop must be running):**
```powershell
.\docker\sdn.ps1 build                                   # build the image (once, or after Dockerfile changes)
.\docker\sdn.ps1                                         # bash shell in the container, project at /project
.\docker\sdn.ps1 sh docker/check_env.sh                  # tool versions + mn --test pingall
.\docker\sdn.ps1 sh docker/connectivity_check.sh geant   # Ryu + pingall on fat_tree / internet2 / geant
```

**Experiments (inside the container, e.g. via `.\docker\sdn.ps1 <command>`):**
```bash
# quick end-to-end check before a long campaign (~25 min, results/smoke)
python3 -m evaluation.experiment --config config/smoke_config.yaml
# everything in config/experiment_config.yaml (3 systems x 10 scenarios x 3 topologies x 30 runs)
python3 -m evaluation.experiment --out results/main
# a subset
python3 -m evaluation.experiment --out results/quick --topologies fat_tree --scenarios S3 S5 --runs 5
# ablation study
python3 -m evaluation.ablation A B C D --topology fat_tree --runs 30 --out results
```
Each run leaves `results/<out>/runs/<topology>_<system>_<scenario>_<run>/` (controller log, `events.jsonl`, iperf3/ping output, `meta.json`) and appends one row to `results/<out>/all_results.csv`.
Re-running the same command resumes: runs already in `all_results.csv` are skipped. A run that fails is logged (`runs/<run>/error.txt`, no CSV row), the campaign continues, the failed runs are listed at the end and retried on the next re-run.

**Analysis (Windows):**
```powershell
.venv\Scripts\python -m evaluation.stats results\main\all_results.csv      # summary.csv (mean, 95% CI, Welch t-test)
.venv\Scripts\python -m analysis.latex_tables results\main\summary.csv     # tables.tex
.venv\Scripts\python -m analysis.plot_results results\main --ablation results   # figures 1-7
.venv\Scripts\python -m evaluation.pareto results\main                     # pareto.json (hit rate)
.venv\Scripts\python -m analysis.plot_pareto results\main                  # figure 8
```

**Estimated campaign time** (≈90 s per run, S6 ≈ 630 s): 3 systems × 10 scenarios × 3 topologies × 30 runs = 2,700 runs ≈ **4.5 days**; Fat-tree alone ≈ 1.5 days. Ablation A–D on one topology, 30 runs: ≈ 1 day (A includes S6).

---

## Phase Checklists

### Phase 1 — Environment Setup
- [x] Docker container (`docker/Dockerfile`, `compose.yaml`, `docker/sdn.ps1`); OVS kernel datapath
- [x] Mininet 2.3.0, OVS 3.1.0, Ryu 4.34 (Python 3.9), iperf3, tcpreplay, tshark, hping3
- [x] Windows `.venv` (Python 3.11) for tests and analysis
- [x] `requirements.txt`, `requirements-ryu.txt`, 3 config files, `.gitignore`, `pytest.ini`

### Phase 2 — Network Topology
- [x] `base_topology.py`, `fat_tree.py`, `wan.py`, `internet2.py`, `geant.py`, `run.py`
- [x] Data + provenance in `topology/data/` (`SOURCES.md`)
- [x] pingall through Ryu: Fat-tree 240/240, Internet2 1122/1122, GÉANT 1560/1560

### Phase 3 — Monitoring Module
- [x] `controller/monitor.py` — port stats → per-direction link utilisation
- [x] Flow stats → per-flow rate (at the flow's first switch)
- [x] Latency probes (timestamped packets, echo-RTT corrected; includes queueing)
- [x] Verified live: target link 21% → 93% under S3

### Phase 4 — Congestion Detector
- [x] N-consecutive trigger, attack confidence, repeat-attack N−1, randomised N (S10)

### Phase 5 — Path Evaluation Engine
- [x] `path_engine.py` — candidates, capacity filter, split allocation
- [x] `scorer.py` — weighted score + drift-plus-penalty
- [x] `hysteresis.py` — deadband + cooldown + reroute/oscillation counts

### Phase 6 — Flow Rule Manager
- [x] `flow_manager.py` — install destination-first, delayed removal of old rules, change count

### Phase 7 — Main Controller
- [x] `routing_base.py` — shared Ryu app: proxy ARP, per-flow rules, polling loop, event log
- [x] `resilient_sdn.py` — control loop with projected loads, ablation switches, decision log

### Phase 8 — Baselines
- [x] `static_routing.py`, `simple_reactive.py`
- [x] `qosentry_stub.py` — policy interface (`QOSENTRY_POLICY=module:function`); without a policy it routes like static and is excluded from the default systems

### Phase 9 — Attack Simulation
- [x] `scenarios.py` (S1–S10), `attacker.py` (target + pair planning, standard floods), `adversarial.py` (pulse, timed, presaturation)
- [x] `traffic/legitimate.py` (iperf3 UDP + ping; bursty mode for Ablation D)
- [x] `traffic/trace_replay.py` (CAIDA/MAWI via tcprewrite + tcpreplay) — **no trace file yet**

### Phase 10 — Evaluation
- [x] `metrics.py`, `experiment.py`, `ablation.py`, `pareto.py`, `stats.py`
- [ ] Full 30-run campaign (Fat-tree, Internet2, GÉANT)
- [ ] Ablation A–D, 30 runs

### Phase 11 — Results and Visualisation
- [x] `plot_results.py` (figures 1–7), `plot_pareto.py` (figure 8), `latex_tables.py`, shared `style.py` (palette validated)
- [ ] Figures and tables from the real campaign

---

## Changes from the Plan (and why)

### Method (affects the paper)
- **DPP → drift form (decided 2026-09-28):** the plan's DPP(P) = Σ Q_ij·(1 − u_post) − V0·K is largest for paths through *busy* links, so in the first live test ResilientSDN never rerouted (current path +0.47 vs best alternative −0.60). Now **DPP(P) = −Σ_{ij∈P} u_ij_post − V0·K(P)** (standard min-drift: the queue backlog the flow adds along its path), reroute if DPP(new) − DPP(current) > ε. Busy and longer paths cost more; V0 = 1, ε = 0.05 unchanged.
- **Attack rates kept as in the plan (decided 2026-09-28):** S2–S5 load the target link to 50–95%, never above 100%, so legitimate loss/throughput barely differ between systems there (only S6 overloads). Differences show in recovery time, latency, secondary congestion, rule changes and oscillations.
- **Score terms normalised to [0,1]:** L(P) = best candidate latency / latency of P; K(P) = new switches / path length.
- **Which flows move:** there is no attack/legitimate classifier in the plan, so on a congested link all flows are candidates, largest first, until the projected load is under the threshold. After each move the projected loads are updated so later decisions see it.
- **No multipath split actuation:** one src/dst flow can't be split without select groups + per-packet hashing. When no single path fits, the event is logged (`unresolved`) instead. `split_traffic` is still computed/tested.
- **Adversarial scenario details the plan left open:** S7 high/low and S8/S9 rates are *total* target-link utilisation (legit included); S8 bursts 8 s every 31 s; S9 preloads up to 4 links on the best detours at 79% with link-disjoint flows, then attacks after 6 s; S10 = one of S7–S9 per run (seeded) with randomised N and cooldown.
- **Target link:** chosen automatically — the most-used link under static routing that is one of the fastest links and not a bridge. Legit and attack flows are placed to cross it, on paths with no slower link (so the target is their bottleneck).
- **S9 preload targets the victims' alternative paths** (lowest-latency paths between the first legitimate flow's edge switches that avoid the target), using flows that don't touch the target or any legit/attack link. **On Fat-tree S9 has no preload:** static shortest-path routing (no ECMP) sends all inter-pod traffic through core c1, so no host pair's traffic crosses the c2–c4 links the controller reroutes onto — an attacker can't reach them. On Internet2/GÉANT S9 works as intended.
- **Legitimate traffic:** 2 × 10 Mbps flows across the target (measured: throughput, loss, ping latency) + 4 × 5 Mbps background flows. Traffic is seeded per (topology, scenario, run), so every system sees identical traffic in a run.
- **Metric definitions** are in `evaluation/metrics.py`: recovery time counts only while the attack runs (censored at attack end); secondary congestion = links other than the target that go over threshold after a reroute moved a flow onto them.
- **Internet2 = OS3E (34 nodes, 42 links); GÉANT 2012 nodes are countries;** WAN capacities scaled so the fastest link = 100 Mbps. See `topology/data/SOURCES.md`.

### Implementation
- **Make-before-break with delayed removal:** new rules are written destination-first; old-path rules are removed 1 s later (packets still queued on old links would otherwise hit a switch with no rule). OF1.3 deletes use `OFPP_ANY`/`OFPG_ANY` (the plan's delete matched nothing).
- **Shared `RoutingBase` Ryu app** for every system; proxy ARP (flooding loops in a Fat-tree); topology known in advance (no LLDP discovery).
- **Latency probes** use a local-experimental ethertype, not LLDP (same measurement, simpler).
- **Static ARP + IPv6 off in Mininet, IPv6 dropped at switches** (fixed a rare lost ping; cut controller packet-ins by 64%).
- **Ryu needs Python 3.9** (`requirements-ryu.txt`); everything else runs on 3.11.
- **`caida_replay.py` + `mawi_replay.py` merged** into `traffic/trace_replay.py` (identical except for the input file).
- **Stats run on Windows**, not at the end of `run_all` (the container has no scipy).
- **QoSentry excluded from the default systems** until a trained policy exists.
- **Added:** `tests/`, `controller/config.py`, `topology/wan.py`, `topology/run.py`, `analysis/style.py`, `docker/`.

## Open Issues from the Team Check (2026-09-28) — fix before writing results
- [ ] **Recovery time rewards flapping** (tester, high): it counts the *first* dip below threshold, even if the link congests again seconds later, so simple reactive looks fastest and static "recovers" when a pulse pauses. Replace with e.g. time above threshold during the attack and/or "stays below for ≥ K s". Post-hoc: recompute from saved `events.jsonl`, no re-run needed.
- [ ] **Pareto hit rate is 100% by construction** (tester, medium): a positive weighted sum's maximiser is always Pareto-optimal. Don't report it as evidence; compare against measured post-reroute outcomes or other weightings.
- [ ] **ResilientSDN doesn't relieve the target on Internet2** (tester, medium): 108 `no_candidate` events in S3 — likely `max_path_hops: 6` too short for WAN detours. Investigate before the WAN campaign.
- [ ] **ResilientSDN holds the attack flow on the target** (tester): on Fat-tree S3 it moves the legitimate flows and the 70 Mbps attack flow stays (link at 72%). Real behaviour — state it in the paper.
- [ ] **Smoke S8 has only one burst** (30 s < 31 s period); the 60 s campaign config has two.
- [ ] Switch failure/disconnect is not handled (flows through a dead switch stay broken) — threats-to-validity sentence.
- [ ] Results vary run to run with the same seed (e.g. S9 loss 30.9% vs 14.1%) — the 30 runs are necessary; consider pinning container CPUs.
- [ ] Analysis CLIs print raw tracebacks on missing/empty CSVs; ablation CLI shows a traceback for bad names.

## Open Questions
- [ ] **QoSentry:** do you have the trained DDQN model/code? Without it Baseline C can't be compared.
- [ ] **CAIDA/MAWI traces:** CAIDA needs a data agreement; MAWI is public. Set `trace.file` in `experiment_config.yaml` to use one.
- [ ] **Campaign size:** ~4.5 days for the full matrix. Run Fat-tree first?

---

## Test Log

| Date | Test | Result |
|------|------|--------|
| 2026-09-28 | Unit tests | 87 passing |
| 2026-09-28 | Smoke: Fat-tree, 3 systems × S1/S3 | Static: no reroutes, never recovers; simple reactive: 36 reroutes, 27 oscillations, 7 secondary congestion; ResilientSDN (after DPP fix): 3 reroutes, recovers in ~8 s, 0 oscillations |
| 2026-09-28 | Smoke: Fat-tree, ResilientSDN × S1–S5, S7–S10 | All run; S7 pulse evades the N=3 trigger (0 reroutes) as designed |
| 2026-09-28 | Analysis pipeline on smoke data | stats, LaTeX tables, figures 1–8, Pareto (hit rate 100%, 18 decisions) |
| 2026-09-28 | Fat-tree, 3 systems × S7–S10 | S7 pulse evades every system's trigger except simple reactive's (20 reroutes); S8: simple reactive 17 reroutes vs ResilientSDN 1 |
| 2026-09-28 | Ablation D (bursty S1), 1 run | single threshold: 1 false reroute; trend trigger: 0 |
| 2026-09-28 | QoSentry stub, S3 | loads, warns, routes as static (no policy) |
| 2026-09-28 | Internet2 + GÉANT, 3 systems × S3/S9 (after fixes) | **S9: simple reactive 12.6% / 18.3% legit loss (200 / 270 reroutes); ResilientSDN 0% loss (3 / 5 reroutes)**; static 0% (never moves onto preloaded paths). GÉANT S3: ResilientSDN brings the target 93% → 72% |

| 2026-09-28 | **Release check (full)** — ruff, syntax, unit tests, image build, pingall ×3, 27-run smoke, secrets, dependency audit | FAIL → fixed: KeyError crash at teardown; see below. After fixes: 100 unit tests, pingall 240/1122/1560 all 0% loss, no controller errors |
| 2026-09-28 | Security review | 2 Medium findings fixed (forged latency probes; packet-in flood). Live test: 1,000 spoofed/unroutable packets → 3 reached the controller |
| 2026-09-28 | Coverage (pytest-cov) | 41% → monitor.py now tested (12 tests); resilient_sdn/routing_base only testable in the container |
| 2026-09-28 | Dependency audit (pip-audit) | requirements.txt clean. Ryu 4.34: 9 DoS CVEs, no fix upstream (malformed OpenFlow from a switch); eventlet 0.30.2: 5 CVEs in unused code. Mitigated: Ryu listens on 127.0.0.1 only |
| 2026-09-28 | Runner hardening for unattended campaigns (`tests/test_experiment_runner.py`, 12 tests) | 112 unit tests passing; container run still to be verified by the tester |

### Bugs found and fixed in the release check / skill review
- **Crash:** rerouting while Mininet stopped switches → `KeyError` in `get_datapath`. `install_path` now refuses (sends nothing) if any switch on the path is disconnected; removals skip gone switches.
- **Security:** latency probes accepted from any port → forged latencies could steer the scorer. Probes now count only on the port facing the claimed sender.
- **Security:** unroutable/spoofed IPv4 reached the controller packet by packet → temporary drop rule (10 s) at the switch.
- **Figure 3** drew never-recovered (censored) runs as recovered at attack end → now plateaus below 1, legend states how many never recovered.
- `docker/check_env.sh` hid the pingall result; `mn --version` on the wrong line.
- CRLF line endings from Windows-side edits broke the container shell scripts → converted; `.gitattributes` keeps `.sh`/`.py` LF.
- Simplify: removed dead `FlowManager.reroute`; public `scorer.objectives()/path_latency()` instead of private calls; `Attacker` de-duplicated; stale `score()` docstring (old DPP) corrected; unused import; ruff import sorting.

### Fixed after team check
- **Runner:** one failing run ended the whole campaign → each run is wrapped: traceback to `runs/<run>/error.txt`, cleanup, next run; summary of done/skipped/failed at the end (Ctrl+C still stops).
- **Runner:** re-running a command duplicated rows → finished (topology, system, scenario, run) keys in `all_results.csv` are skipped (resume).
- **Runner:** typos in `--systems/--scenarios/--topologies` crashed mid-campaign, `--runs 0` silently used the config default → all inputs validated before the first run, clean usage error.
- **Ping:** `sh -c 'ping ... > path'` lost the latency file when the results path had a space → ping started without a shell, stdout to the file.

### Bugs found and fixed during testing
- DPP formula never allowed a reroute → drift form (method decision above).
- Ryu not listening yet when Mininet started → runner waits for port 6653.
- Recovery time counted the attack *ending* as recovery → only samples during the attack count.
- Throughput read iperf3's *sender* rate → now `sum_received`.
- S9 preload flows shared an uplink and saturated it before the attack → preload flows must be link-disjoint, and must not use any legit/attack link (they had overloaded the attack's own uplink to 125%).
- S9 preloaded detours of the target *link*, not the paths flows are actually rerouted onto → now the victims' alternative paths; path search on a copy of the graph without the target (filtering the path generator hung on WANs).
- GÉANT: the attack was throttled by a 10 Mbps hop before reaching the 100 Mbps target (target stayed at 45%) → pairs must have no link slower than the target.
- Packets queued on the old path were dropped after a reroute → old rules removed 1 s later.
- LaTeX caption had an extra `}`; ties were bolded arbitrarily → fixed.
- Figure CI whiskers went below 0; Pareto legend covered points → fixed.

---

## Work Log

| Date | Work done | Notes |
|------|-----------|-------|
| 2026-09-28 | Created `IMPLEMENTATION_PLAN.md` and `PROGRESS.md` | Project folder was empty |
| 2026-09-28 | Folder structure, requirements, configs, `.venv` | |
| 2026-09-28 | Detector, path engine, scorer, hysteresis + tests | |
| 2026-09-28 | Docker environment; Day 1: Ryu + Fat-tree pingall 240/240 | |
| 2026-09-28 | Phase 2: Internet2 (OS3E) + GÉANT; static ARP / IPv6 fix | |
| 2026-09-28 | Phases 3, 7–11 built (monitor, main controller, baselines, attacks, traffic, evaluation, analysis) | Followed ponytail `lite` rules manually (plugin not active this session) |
| 2026-09-28 | Integration testing in the container; DPP changed to drift form; 11 bugs fixed | See Test Log; `config/smoke_config.yaml` kept for quick checks |
| 2026-09-28 | Skill review (release-check, security-review, simplify, testing, visualization); crash + 2 security issues + Fig. 3 fixed | 100 tests |
| 2026-09-28 | `/team` check: tester end-to-end (PASS WITH ISSUES, see Open Issues); developer made the runner crash-safe (resume, per-run failures, input validation, ping path fix); tester verified in container | 112 tests |
| 2026-09-28 | **Fat-tree campaign started:** 3 systems × 10 scenarios × 5 runs = 150 runs (~5 h) → `results/fattree_5runs/` | Re-run the same command to resume if interrupted |
| 2026-09-29 | WSL2 Ubuntu 24.04 set up on D: (Mininet 2.3.0, OVS 3.3.9, Ryu 4.34/Py3.9) + `run.sh`; 21-role company agents + new `/team` skill (global) | WSL Mininet start confirmed; live WSL tests after the campaign |
| 2026-09-29 | Campaign stopped at 75/150 (the Windows-side client was closed after ~2 h); resumed **detached** (`docker compose run -d --name sdn-campaign`), finished runs skipped | Watch: `docker logs -f sdn-campaign` or `results/fattree_5runs/campaign.log` |
| 2026-09-29 | Project cleaned (caches, superseded smoke/demo results); **final report** `reports/ResilientSDN_Report.pdf` (19 pages, contents, diagrams, design, setup, implementation, results, limitations, appendices; private, gitignored) | Rebuild after the campaign: `.venv\Scripts\python reports\make_report.py` |
| 2026-09-29 | Author name added to the report; PROGRESS "Right Now" + "Next Steps" sections | Campaign 76/150, ETA ≈ 05:50 |
| 2026-09-29 | NSFNET added (Internet Topology Zoo); `analysis/run_report.py` builds `report.pdf` after every run; correctness checks; recovery metric fixed (time above threshold, stable recovery) and old CSVs recomputed; S11 (2 min overload) added; Internet2 `max_path_hops` 12 | Backed up as `all_results.before_recompute.csv` |
| 2026-09-29 to 09-30 | Final campaign in WSL: fat-tree 165 runs, WAN 225 runs (NSFNET, Internet2, GEANT), ablation A-D 220 runs | 0 failed; ablation first start crashed on a typo (comma inside a comment), fixed + test, restarted |
| 2026-09-30 | Final QA PASS; skeptical data review; wording made defensible; `analysis/verify_claims.py` (raw files vs CSV, paired tests); `reports/ResilientSDN_Final_Results.pdf`; docs, demo.sh, run_full.sh; files arranged (docs/, docker/) | 127 tests |
| 2026-10-01 | Any-topology support (`topology.add`, auto-discovery, loader repairs messy Zoo files); live tests on Abilene, Arnes, Renater2010; final PDF generator numbers new files | 128 tests |
| 2026-10-01 | Weak points investigated with systematic debugging: root cause of the Internet2 hold found; two fix attempts rejected (negative result kept as `rsdn_relief_bypass`, off by default); S7 shown harmless; `reports/ResilientSDN_Final_Results_v2.pdf` | 130 tests; see "Weak points: status" |
