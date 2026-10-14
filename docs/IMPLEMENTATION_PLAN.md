# ResilientSDN — Complete Implementation Plan

## Project Folder Structure

```
project/
│
├── topology/
│   ├── fat_tree.py            # Fat-tree k=4 Mininet topology
│   ├── internet2.py           # Internet2 34-node topology
│   ├── geant.py               # GÉANT 40-node topology
│   └── base_topology.py       # Shared topology base class
│
├── controller/
│   ├── resilient_sdn.py       # Main Ryu controller app
│   ├── monitor.py             # Link/flow statistics collector
│   ├── detector.py            # Trend-based congestion detector
│   ├── path_engine.py         # Path generation + capacity filter
│   ├── scorer.py              # Multi-objective DPP-grounded scorer
│   ├── hysteresis.py          # Hysteresis + cooldown manager
│   └── flow_manager.py        # OpenFlow rule installer
│
├── baselines/
│   ├── static_routing.py      # Baseline A: static shortest path
│   ├── simple_reactive.py     # Baseline B: threshold → shortest alt
│   └── qosentry_stub.py       # Baseline C: QoSentry DDQN interface
│
├── attack/
│   ├── attacker.py            # DDoS/LFA traffic generator
│   ├── scenarios.py           # 10 predefined attack scenarios
│   └── adversarial.py         # Adversarial attack patterns (S7–S10)
│
├── traffic/
│   ├── legitimate.py          # Legitimate flow generator
│   ├── caida_replay.py        # CAIDA trace replay via tcpreplay
│   └── mawi_replay.py         # MAWI trace replay
│
├── evaluation/
│   ├── metrics.py             # Metric collection (throughput, loss, etc.)
│   ├── experiment.py          # Automated experiment runner
│   ├── ablation.py            # Ablation study runner (A–E)
│   ├── pareto.py              # Pareto front computation
│   └── stats.py               # Confidence intervals, t-tests
│
├── analysis/
│   ├── plot_results.py        # Matplotlib graphs
│   ├── plot_pareto.py         # Pareto front visualization
│   └── latex_tables.py        # Auto-generate IEEE paper tables
│
├── config/
│   ├── network_config.yaml    # Link capacities, latencies
│   ├── controller_config.yaml # Thresholds, weights, cooldown
│   └── experiment_config.yaml # Scenarios, iterations
│
├── results/                   # Auto-generated CSV + graphs
└── requirements.txt           # Python dependencies
```

> Note: the original plan listed `controller/resiient_sdn.py` (typo); Phase 7 uses `resilient_sdn.py`, which is the name used here.

---

## PHASE 1 — ENVIRONMENT SETUP

### What to Install

```bash
# Python environment
pip install ryu networkx pandas matplotlib scipy numpy pyyaml

# Mininet (Linux/WSL required)
sudo apt-get install mininet

# Open vSwitch
sudo apt-get install openvswitch-switch

# Traffic tools
sudo apt-get install iperf3 tcpreplay tshark hping3

# Verify Ryu
ryu-manager --version

# Verify Mininet
sudo mn --test pingall
```

### File: `requirements.txt`

```
ryu>=4.34
networkx>=3.0
pandas>=2.0
matplotlib>=3.7
scipy>=1.10
numpy>=1.24
pyyaml>=6.0
```

### File: `config/controller_config.yaml`

```yaml
controller:
  polling_interval: 2          # seconds between stat polls
  utilisation_threshold: 0.80  # trigger threshold (80%)
  safe_threshold: 0.80         # max allowed post-reroute utilisation
  N_intervals: 3               # consecutive intervals to confirm congestion
  cooldown_seconds: 30         # minimum time between rerouting events
  hysteresis_epsilon: 0.05     # DPP deadband threshold
  dpp_weight_V0: 1.0           # DPP trade-off parameter

scoring_weights:
  w1_capacity: 0.40            # residual capacity weight
  w2_latency: 0.30             # latency weight
  w3_rule_cost: 0.30           # rule-change penalty weight

adversarial:
  randomise_N: false           # set true for S10 adversarial scenarios
  N_min: 2
  N_max: 5
  randomise_cooldown: false
  cooldown_min: 20
  cooldown_max: 45
```

---

## PHASE 2 — NETWORK TOPOLOGY

### File: `topology/base_topology.py`

**Purpose:** Shared base class for all topologies.

**What it does:**
- Creates Mininet network with custom link bandwidth and latency
- Returns graph object (NetworkX) for path computation
- Provides host/switch references

**Key method signatures:**

```python
class BaseTopology:
    def build(self) -> Mininet
    def get_graph(self) -> nx.Graph
    def get_hosts(self) -> list
    def get_switches(self) -> list
    def start(self)
    def stop(self)
```

### File: `topology/fat_tree.py`

**Purpose:** Fat-tree k=4 datacenter topology (20 switches, 16 hosts)

**Structure:**

```
k=4 Fat-tree:
  Core switches:  (k/2)² = 4
  Aggregation:    k*(k/2) = 8
  Edge switches:  k*(k/2) = 8
  Hosts per edge: k/2 = 2
  Total hosts:    16
```

**Key code pattern:**

```python
from mininet.topo import Topo

class FatTree(Topo):
    def build(self, k=4):
        # Create core switches
        # Create aggregation switches
        # Create edge switches
        # Create hosts
        # Wire them up with correct link bandwidth/latency
        # Return topology
```

### File: `topology/internet2.py`

**Purpose:** Internet2 WAN topology (34 nodes, real backbone link capacities)

**Node list (sample):**
Atlanta, Chicago, Denver, Houston, Kansas_City, Los_Angeles, New_York, Seattle, Washington_DC, ...

- **Link data:** hardcoded from public Internet2 topology documentation
- **Link capacity:** 10–100 Gbps (scale down for simulation)
- **Link latency:** based on geographic distance

### File: `topology/geant.py`

**Purpose:** GÉANT European research network (40+ nodes)

**Node list (sample):**
Amsterdam, Athens, Brussels, Budapest, Copenhagen, Dublin, Frankfurt, Geneva, Helsinki, Lisbon, London, Madrid, Milan, Oslo, Paris, Prague, Rome, Stockholm, Vienna, Warsaw, Zurich, ...

---

## PHASE 3 — MONITORING MODULE

### File: `controller/monitor.py`

**Purpose:** Collect link and flow statistics from all switches every polling interval.

**What it measures:**
- Bytes transmitted per port (`OFPPortStatsRequest`)
- Packets per flow (`OFPFlowStatsRequest`)
- Computed link utilisation
- Latency via LLDP probing

**Core structure:**

```python
class NetworkMonitor:
    def __init__(self, controller, config):
        self.controller = controller
        self.config = config
        self.link_stats = {}   # {(sw_i, sw_j): {bytes, utilisation}}
        self.flow_stats = {}   # {flow_id: {bytes, packets, rate}}
        self.latency = {}      # {(sw_i, sw_j): latency_ms}

    def start_polling(self):
        # Schedule periodic stat requests every config.polling_interval seconds

    def request_port_stats(self, datapath):
        # Send OFPPortStatsRequest to switch

    def request_flow_stats(self, datapath):
        # Send OFPFlowStatsRequest to switch

    def on_port_stats_reply(self, ev):
        # Receive reply, compute:
        #   bytes_delta = current_bytes - previous_bytes
        #   utilisation = bytes_delta / (interval * link_capacity)
        # Store in self.link_stats

    def probe_latency(self):
        # Send LLDP packets, measure round-trip time
        # Store in self.latency

    def get_utilisation(self, link) -> float:
        # Return current utilisation for a given link

    def get_all_link_stats(self) -> dict:
        # Return full link_stats dictionary
```

---

## PHASE 4 — CONGESTION DETECTOR

### File: `controller/detector.py`

**Purpose:** Implement trend-based congestion detection. Not a single-threshold trigger — requires N consecutive intervals above threshold.

**Core logic:**

```python
class CongestionDetector:
    def __init__(self, config):
        self.threshold = config.utilisation_threshold  # 0.80
        self.N = config.N_intervals                    # 3
        self.counters = {}        # {link: consecutive_high_count}
        self.attack_history = {}  # {link: last_attack_time}

    def update(self, link_stats: dict) -> list:
        """
        Called every polling interval.
        Returns list of links that have triggered congestion.
        """
        triggered = []
        for link, stats in link_stats.items():
            if stats['utilisation'] > self.threshold:
                self.counters[link] = self.counters.get(link, 0) + 1
                if self.counters[link] >= self.N:
                    triggered.append(link)
            else:
                self.counters[link] = 0  # reset counter on drop below threshold
        return triggered

    def get_attack_confidence(self, link) -> float:
        """
        Returns fraction of recent intervals that were above threshold.
        Used for logging and paper reporting.
        """

    def adapt_N_for_repeated_attacks(self, link) -> int:
        """
        If link was attacked recently, reduce N by 1 (respond faster).
        Adversarial robustness mechanism.
        """

    def randomise_thresholds(self):
        """
        For adversarial scenarios S10: randomise N and cooldown.
        N ~ Uniform(N_min, N_max)
        """
```

---

## PHASE 5 — PATH EVALUATION ENGINE

This is the core of the project. Three files.

### File: `controller/path_engine.py`

**Purpose:** Find all candidate alternate paths, apply capacity filter.

```python
import networkx as nx

class PathEngine:
    def __init__(self, graph: nx.Graph, config):
        self.G = graph
        self.safe_threshold = config.safe_threshold  # 0.80

    def find_candidates(self, src, dst, congested_link,
                        traffic_to_move, link_stats) -> list:
        """
        1. Find all simple paths from src to dst
        2. Remove paths that use the congested link
        3. Apply capacity filter to each remaining path
        4. Return only paths that pass
        """
        all_paths = list(nx.all_simple_paths(
            self.G, source=src, target=dst, cutoff=6
        ))
        candidates = []
        for path in all_paths:
            if congested_link in self._get_links(path):
                continue  # skip paths using congested link
            if self._passes_capacity_filter(path, traffic_to_move, link_stats):
                candidates.append(path)
        return candidates

    def _passes_capacity_filter(self, path, traffic_to_move, link_stats) -> bool:
        """
        For every link in the path:
            post_reroute = current_load + traffic_to_move
            If any link post_reroute > safe_threshold → REJECT
        """
        for link in self._get_links(path):
            current = link_stats[link]['bytes_per_sec']
            capacity = link_stats[link]['capacity']
            post_utilisation = (current + traffic_to_move) / capacity
            if post_utilisation > self.safe_threshold:
                return False
        return True

    def compute_post_reroute_utilisation(self, path, traffic_to_move,
                                         link_stats) -> float:
        """Returns worst-case post-reroute utilisation across all links in path."""
        worst = 0.0
        for link in self._get_links(path):
            current = link_stats[link]['bytes_per_sec']
            capacity = link_stats[link]['capacity']
            post = (current + traffic_to_move) / capacity
            worst = max(worst, post)
        return worst

    def _get_links(self, path) -> list:
        """Convert node list to edge list: [A,B,C] → [(A,B),(B,C)]"""
        return [(path[i], path[i+1]) for i in range(len(path)-1)]

    def split_traffic(self, candidates, traffic_to_move,
                      link_stats) -> dict:
        """
        Fallback when no single path can handle all traffic.
        Distribute proportionally by residual capacity.
        Returns {path: traffic_allocated}
        """
        residuals = {}
        for path in candidates:
            min_residual = float('inf')
            for link in self._get_links(path):
                cap = link_stats[link]['capacity']
                cur = link_stats[link]['bytes_per_sec']
                residual = cap * self.safe_threshold - cur
                min_residual = min(min_residual, residual)
            residuals[tuple(path)] = max(0, min_residual)

        total_residual = sum(residuals.values())
        if total_residual == 0:
            return {}

        allocation = {}
        for path, residual in residuals.items():
            allocation[path] = (residual / total_residual) * traffic_to_move
        return allocation
```

### File: `controller/scorer.py`

**Purpose:** Multi-objective DPP-grounded path scorer.

```python
class PathScorer:
    def __init__(self, config, link_stats):
        self.w1 = config.scoring_weights['w1_capacity']
        self.w2 = config.scoring_weights['w2_latency']
        self.w3 = config.scoring_weights['w3_rule_cost']
        self.link_stats = link_stats
        self.V0 = config.dpp_weight_V0

    def score(self, path, traffic_to_move,
              current_path, latency) -> float:
        """
        Score(P) = w1*C(P) + w2*L(P) - w3*K(P)

        DPP-grounded formulation:
        a*(t) = argmax Σ Q_ij * [C_ij - T_ij_post] - V0 * K(a)
        """
        C = self._capacity_score(path, traffic_to_move)
        L = self._latency_score(path, latency)
        K = self._rule_cost(path, current_path)
        score = self.w1 * C + self.w2 * L - self.w3 * K
        return score

    def dpp_score(self, path, traffic_to_move,
                  queue_levels, current_path) -> float:
        """
        True DPP score (for theoretical experiments):
        DPP(a) = Σ Q_ij(t) * [C_ij - T_ij_post(a)] - V0 * K(a)
        """
        dpp = 0.0
        for link in self._get_links(path):
            Q = queue_levels[link]  # current utilisation
            C = self.link_stats[link]['capacity']
            T_post = self.link_stats[link]['bytes_per_sec'] + traffic_to_move
            dpp += Q * (C - T_post)
        dpp -= self.V0 * self._rule_cost(path, current_path)
        return dpp

    def _capacity_score(self, path, traffic_to_move) -> float:
        """
        Minimum residual capacity across all links in path,
        after rerouting. Normalised to [0,1].
        Higher = more headroom remaining.
        """
        worst_post = 0.0
        for link in self._get_links(path):
            cap = self.link_stats[link]['capacity']
            cur = self.link_stats[link]['bytes_per_sec']
            post = (cur + traffic_to_move) / cap
            worst_post = max(worst_post, post)
        return 1.0 - worst_post

    def _latency_score(self, path, latency: dict) -> float:
        """
        1 / total_path_latency (ms).
        Normalised — higher = faster path.
        """
        total_latency = sum(
            latency.get(link, 5.0)  # default 5ms if unknown
            for link in self._get_links(path)
        )
        return 1.0 / max(total_latency, 0.1)

    def _rule_cost(self, path, current_path) -> int:
        """
        Number of switches where the forwarding rule changes.
        = number of nodes that differ between current and new path.
        """
        current_set = set(current_path)
        new_set = set(path)
        return len(new_set - current_set)

    def rank_candidates(self, candidates, traffic_to_move,
                        current_path, latency) -> list:
        """Returns candidates sorted by score descending."""
        scored = [
            (path, self.score(path, traffic_to_move, current_path, latency))
            for path in candidates
        ]
        return sorted(scored, key=lambda x: x[1], reverse=True)

    def _get_links(self, path) -> list:
        return [(path[i], path[i+1]) for i in range(len(path)-1)]
```

### File: `controller/hysteresis.py`

**Purpose:** Implement DPP-deadband hysteresis and cooldown. Prevents chattering near Lyapunov optimum.

```python
import time

class HysteresisManager:
    def __init__(self, config):
        self.epsilon = config.hysteresis_epsilon  # DPP deadband
        self.cooldown = config.cooldown_seconds   # seconds
        self.last_reroute_time = {}  # {flow: timestamp}
        self.current_dpp = {}        # {flow: current DPP value}

    def should_reroute(self, flow_id, new_path_dpp,
                       current_path_dpp, link) -> bool:
        """
        Reroute only if ALL conditions are satisfied:
        1. DPP improvement exceeds deadband epsilon
        2. Cooldown period has elapsed
        3. New path is significantly better (not marginally)
        """
        # Condition 1: DPP improvement
        dpp_improvement = new_path_dpp - current_path_dpp
        if dpp_improvement <= self.epsilon:
            return False

        # Condition 2: Cooldown
        last = self.last_reroute_time.get(flow_id, 0)
        if time.time() - last < self.cooldown:
            return False

        return True

    def record_reroute(self, flow_id, new_dpp):
        """Record that a rerouting event occurred."""
        self.last_reroute_time[flow_id] = time.time()
        self.current_dpp[flow_id] = new_dpp

    def get_time_since_last_reroute(self, flow_id) -> float:
        last = self.last_reroute_time.get(flow_id, 0)
        return time.time() - last

    def get_reroute_count(self, flow_id) -> int:
        """For experiment metrics."""
        return self.reroute_counts.get(flow_id, 0)
```

---

## PHASE 6 — FLOW RULE MANAGER

### File: `controller/flow_manager.py`

**Purpose:** Install, update, and remove OpenFlow flow rules on switches.

```python
from ryu.ofproto import ofproto_v1_3

class FlowManager:
    def __init__(self, controller):
        self.controller = controller
        self.installed_rules = {}  # Track what rules are installed where
        self.rule_change_count = 0  # Metric for paper

    def install_path(self, path, match, priority=100):
        """
        Install flow rules along an entire path.
        For each switch in path, install:
            Match: packet headers
            Action: forward to next switch port
        """
        for i, switch in enumerate(path[:-1]):
            next_hop = path[i+1]
            out_port = self._get_port(switch, next_hop)
            self._install_rule(switch, match, out_port, priority)
            self.rule_change_count += 1

    def remove_old_rules(self, old_path, match):
        """Remove rules from switches that are no longer on the active path."""
        for switch in old_path:
            if switch not in self.new_path:
                self._delete_rule(switch, match)
                self.rule_change_count += 1

    def _install_rule(self, switch, match, out_port, priority):
        """Low-level OpenFlow rule installation via Ryu API."""
        datapath = self.controller.get_datapath(switch)
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        actions = [parser.OFPActionOutput(out_port)]
        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions
        )]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst
        )
        datapath.send_msg(mod)

    def _delete_rule(self, switch, match):
        """Remove a specific flow rule from a switch."""
        datapath = self.controller.get_datapath(switch)
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            match=match
        )
        datapath.send_msg(mod)

    def get_rule_change_count(self) -> int:
        return self.rule_change_count

    def reset_rule_change_count(self):
        self.rule_change_count = 0
```

---

## PHASE 7 — MAIN CONTROLLER

### File: `controller/resilient_sdn.py`

**Purpose:** Main Ryu application that wires all modules together.

```python
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import set_ev_cls, MAIN_DISPATCHER

from controller.monitor import NetworkMonitor
from controller.detector import CongestionDetector
from controller.path_engine import PathEngine
from controller.scorer import PathScorer
from controller.hysteresis import HysteresisManager
from controller.flow_manager import FlowManager

class ResilientSDN(app_manager.RyuApp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = load_config('config/controller_config.yaml')
        self.monitor = NetworkMonitor(self, config)
        self.detector = CongestionDetector(config)
        self.path_engine = None  # initialised after topology loaded
        self.scorer = None
        self.hysteresis = HysteresisManager(config)
        self.flow_mgr = FlowManager(self)
        self.current_paths = {}  # {flow_id: current_path}

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def on_port_stats(self, ev):
        self.monitor.on_port_stats_reply(ev)
        self._control_loop()

    def _control_loop(self):
        """
        Main decision loop — runs every polling interval.
        1. Get current link stats
        2. Detect congested links
        3. For each congested link:
            a. Find legitimate flows on that link
            b. Generate candidate alternate paths
            c. Apply capacity filter
            d. Score candidates
            e. Check hysteresis
            f. If approved → install new flow rules
        """
        link_stats = self.monitor.get_all_link_stats()
        congested = self.detector.update(link_stats)

        for link in congested:
            flows = self._get_affected_flows(link)
            for flow in flows:
                traffic = self._estimate_flow_traffic(flow, link_stats)
                src, dst = flow.src, flow.dst

                # Generate + filter candidates
                candidates = self.path_engine.find_candidates(
                    src, dst, link, traffic, link_stats
                )

                if not candidates:
                    # No single path — try splitting
                    allocation = self.path_engine.split_traffic(
                        self._all_paths(src, dst), traffic, link_stats
                    )
                    if allocation:
                        self._apply_split(flow, allocation)
                    continue

                # Score candidates
                latency = self.monitor.latency
                ranked = self.scorer.rank_candidates(
                    candidates, traffic,
                    self.current_paths.get(flow.id, []), latency
                )
                best_path, best_score = ranked[0]

                # Hysteresis check
                current_path = self.current_paths.get(flow.id, [])
                new_dpp = self.scorer.dpp_score(
                    best_path, traffic,
                    self._get_queue_levels(link_stats), current_path
                )
                current_dpp = self.scorer.dpp_score(
                    current_path, 0,
                    self._get_queue_levels(link_stats), current_path
                ) if current_path else -999

                if self.hysteresis.should_reroute(flow.id, new_dpp, current_dpp, link):
                    # Install rules
                    match = self._build_match(flow)
                    self.flow_mgr.remove_old_rules(current_path, match)
                    self.flow_mgr.install_path(best_path, match)
                    self.current_paths[flow.id] = best_path
                    self.hysteresis.record_reroute(flow.id, new_dpp)
                    self.logger.info(f"Rerouted flow {flow.id} to {best_path}")
```

---

## PHASE 8 — BASELINES

### File: `baselines/static_routing.py`

```python
class StaticRouting:
    """Baseline A: install shortest path at startup, never change it."""
    def __init__(self, graph):
        self.G = graph

    def get_path(self, src, dst) -> list:
        return nx.shortest_path(self.G, src, dst, weight='latency')

    def update(self, link_stats):
        pass  # Does nothing — static
```

### File: `baselines/simple_reactive.py`

```python
class SimpleReactive:
    """
    Baseline B: if utilisation > threshold → pick shortest alternate path.
    No capacity check. No scoring. No hysteresis.
    """
    def __init__(self, graph, config, flow_manager):
        self.G = graph
        self.threshold = config.utilisation_threshold
        self.flow_mgr = flow_manager

    def update(self, link_stats):
        for link, stats in link_stats.items():
            if stats['utilisation'] > self.threshold:
                # Find shortest path avoiding this link
                G_temp = self.G.copy()
                G_temp.remove_edge(*link)
                for flow in self._get_flows_on(link):
                    try:
                        alt = nx.shortest_path(
                            G_temp, flow.src, flow.dst, weight='latency'
                        )
                        match = self._build_match(flow)
                        self.flow_mgr.install_path(alt, match)
                    except nx.NetworkXNoPath:
                        pass  # No alternate exists
```

---

## PHASE 9 — ATTACK SIMULATION

### File: `attack/scenarios.py`

```python
SCENARIOS = {
    'S1':  {'name': 'No attack',       'attackers': 0, 'rate_mbps': 0},
    'S2':  {'name': 'Low single',      'attackers': 1, 'rate_mbps': 30},
    'S3':  {'name': 'High single',     'attackers': 1, 'rate_mbps': 70},
    'S4':  {'name': 'Distributed 3x',  'attackers': 3, 'rate_mbps': 20},
    'S5':  {'name': 'Heavy 5x',        'attackers': 5, 'rate_mbps': 15},
    'S6':  {'name': 'Sustained 10min', 'attackers': 3, 'rate_mbps': 30,
            'duration': 600},
    'S7':  {'name': 'Threshold evasion', 'type': 'adversarial',
            'pattern': 'pulse', 'high': 85, 'low': 65, 'interval': 4},
    'S8':  {'name': 'Cooldown exploit', 'type': 'adversarial',
            'pattern': 'timed', 'interval': 31},
    'S9':  {'name': 'Capacity presaturation', 'type': 'adversarial',
            'preload_alt_paths': 0.79},
    'S10': {'name': 'Randomised thresholds', 'type': 'adversarial',
            'same_as': ['S7', 'S8', 'S9'], 'randomise': True},
}
```

### File: `attack/attacker.py`

```python
class Attacker:
    def run_scenario(self, net, scenario_id, target_host, target_link):
        """
        Generate attack traffic for a given scenario.
        Uses iperf3 for synthetic flood or hping3 for SYN flood.
        """
        s = SCENARIOS[scenario_id]
        if s.get('type') == 'adversarial':
            self._run_adversarial(net, s, target_host)
        else:
            self._run_standard(net, s, target_host)

    def _run_standard(self, net, scenario, target):
        """Start N iperf3 UDP flood streams at specified rate."""
        attack_hosts = self._get_attack_hosts(net, scenario['attackers'])
        for host in attack_hosts:
            host.popen(
                f"iperf3 -c {target.IP()} -u -b {scenario['rate_mbps']}M "
                f"-t {scenario.get('duration', 60)}"
            )

    def _run_adversarial(self, net, scenario, target):
        """Run pulsed or timed attack patterns for adversarial scenarios."""
        if scenario['pattern'] == 'pulse':
            self._pulse_attack(net, scenario, target)
        elif scenario['pattern'] == 'timed':
            self._timed_attack(net, scenario, target)
```

---

## PHASE 10 — EVALUATION AND EXPERIMENT AUTOMATION

### File: `evaluation/metrics.py`

```python
class MetricsCollector:
    def __init__(self):
        self.data = []

    def record(self, timestamp, system, scenario, topology,
               throughput, packet_loss, latency, recovery_time,
               secondary_congestion, rule_changes, oscillations,
               controller_cpu):
        self.data.append({
            'timestamp': timestamp,
            'system': system,
            'scenario': scenario,
            'topology': topology,
            'throughput_mbps': throughput,
            'packet_loss_pct': packet_loss,
            'latency_ms': latency,
            'recovery_time_s': recovery_time,
            'secondary_congestion': secondary_congestion,
            'rule_changes': rule_changes,
            'oscillations': oscillations,
            'controller_cpu_pct': controller_cpu,
        })

    def to_csv(self, path):
        import pandas as pd
        pd.DataFrame(self.data).to_csv(path, index=False)
```

### File: `evaluation/experiment.py`

```python
class ExperimentRunner:
    """
    Fully automated experiment runner.
    Runs all combinations of:
        systems × scenarios × topologies
    Each combination run 30 times.
    """
    SYSTEMS = ['static', 'simple_reactive', 'resilient_sdn', 'qosentry']
    SCENARIOS = ['S1', 'S2', 'S3', 'S4', 'S5', 'S6',
                 'S7', 'S8', 'S9', 'S10']
    TOPOLOGIES = ['fat_tree', 'internet2', 'geant']
    N_RUNS = 30

    def run_all(self):
        for topology in self.TOPOLOGIES:
            for system in self.SYSTEMS:
                for scenario in self.SCENARIOS:
                    for run in range(self.N_RUNS):
                        result = self.run_single(
                            topology, system, scenario, run
                        )
                        self.metrics.record(**result)
                        self.cleanup()
        self.metrics.to_csv('results/all_results.csv')
        self.stats.compute_confidence_intervals('results/all_results.csv')

    def run_single(self, topology, system, scenario, run_id):
        # 1. Start Mininet with topology
        # 2. Start Ryu controller with chosen system
        # 3. Wait for network convergence
        # 4. Start legitimate traffic (iperf3 + CAIDA/MAWI replay)
        # 5. Start attack (attacker.run_scenario)
        # 6. Collect metrics for experiment duration
        # 7. Return metrics dict
        pass

    def cleanup(self):
        # mn -c (clean Mininet state)
        # Kill all iperf3/tcpreplay processes
        pass
```

### File: `evaluation/ablation.py`

Five ablation experiments isolating each mechanism:

```python
class AblationStudy:
    def run_experiment_A(self):
        """Does capacity verification prevent secondary congestion?"""
        # Compare: simple_reactive vs our system (capacity filter only)
        # Measure: secondary congestion events

    def run_experiment_B(self):
        """Does hysteresis reduce oscillation?"""
        # Compare: our system without hysteresis vs with hysteresis
        # Measure: oscillation count, rule changes

    def run_experiment_C(self):
        """Does multi-objective scoring outperform shortest path?"""
        # Compare: shortest alternate vs multi-objective scored
        # Measure: throughput, latency, rule changes

    def run_experiment_D(self):
        """Does trend-based trigger reduce false positives?"""
        # Compare: single threshold vs N-interval persistent trigger
        # Test on S1 (no attack) with bursty legitimate traffic
        # Measure: unnecessary rerouting events

    def run_experiment_E(self):
        """Does full system outperform all baselines?"""
        # Compare: all 4 systems across all 10 scenarios
        # Measure: all 8 metrics + 95% CI
```

### File: `evaluation/pareto.py`

```python
class ParetoAnalysis:
    def compute_pareto_front(self, candidates, traffic_to_move, link_stats):
        """
        For a set of candidate paths, compute the Pareto-optimal subset.
        Objectives: maximise C(P), maximise L(P), minimise K(P)
        A path P dominates Q if P is ≥ Q on all objectives and > on at least one.
        """
        dominated = set()
        for i, p in enumerate(candidates):
            for j, q in enumerate(candidates):
                if i == j:
                    continue
                if self._dominates(p, q, traffic_to_move, link_stats):
                    dominated.add(j)
        pareto_front = [c for i, c in enumerate(candidates)
                        if i not in dominated]
        return pareto_front

    def scalarisation_hit_rate(self, all_instances):
        """
        For each path selection instance, check if scalarisation
        selected a Pareto-optimal path. Report hit rate.
        """
        hits = 0
        for instance in all_instances:
            pareto = self.compute_pareto_front(
                instance['candidates'],
                instance['traffic'],
                instance['link_stats']
            )
            selected = instance['scalarisation_selected']
            if selected in pareto:
                hits += 1
        return hits / len(all_instances)
```

### File: `evaluation/stats.py`

```python
from scipy import stats
import numpy as np

class StatisticalAnalysis:
    def confidence_interval(self, data, confidence=0.95):
        n = len(data)
        mean = np.mean(data)
        se = stats.sem(data)
        ci = se * stats.t.ppf((1 + confidence) / 2, n - 1)
        return mean, ci

    def welch_t_test(self, group1, group2):
        """Compare two systems. Returns t-statistic and p-value."""
        t, p = stats.ttest_ind(group1, group2, equal_var=False)
        return t, p

    def compute_all_intervals(self, results_csv):
        """
        For each (system, scenario, topology, metric) combination:
        compute mean ± 95% CI and Welch's t-test vs simple_reactive.
        Save as summary CSV.
        """
        import pandas as pd
        df = pd.read_csv(results_csv)
        summary = []
        metrics = ['throughput_mbps', 'packet_loss_pct', 'latency_ms',
                   'recovery_time_s', 'secondary_congestion',
                   'rule_changes', 'oscillations', 'controller_cpu_pct']
        for system in df['system'].unique():
            for scenario in df['scenario'].unique():
                for topology in df['topology'].unique():
                    subset = df[(df.system == system) &
                                (df.scenario == scenario) &
                                (df.topology == topology)]
                    for metric in metrics:
                        mean, ci = self.confidence_interval(subset[metric])
                        summary.append({
                            'system': system, 'scenario': scenario,
                            'topology': topology, 'metric': metric,
                            'mean': mean, 'ci_95': ci
                        })
        pd.DataFrame(summary).to_csv('results/summary.csv', index=False)
```

---

## PHASE 11 — RESULTS AND VISUALISATION

### File: `analysis/plot_results.py`

Generates all paper figures:

```
# Figure 1: Throughput comparison across systems (bar chart with CI)
# Figure 2: Packet loss vs attack intensity (line chart)
# Figure 3: Recovery time CDF
# Figure 4: Secondary congestion events (our system = 0 baseline)
# Figure 5: Rule changes per rerouting event
# Figure 6: Ablation — contribution of each mechanism
# Figure 7: Adversarial robustness (S7–S10 performance)
# Figure 8: Pareto front visualisation
```

---

## 7-DAY MVP TIMELINE

| Day | Tasks | Output |
|-----|-------|--------|
| Day 1 | Install tools, set up Fat-tree topology in Mininet, verify H1→H2 connectivity | Working topology + basic Ryu controller |
| Day 2 | Implement `monitor.py`, verify stat collection from all switches | Live link utilisation readings |
| Day 3 | Implement `detector.py` + `path_engine.py` (capacity filter), run S2 scenario | Correct congestion detection + candidate paths |
| Day 4 | Implement `scorer.py` + `hysteresis.py` + `flow_manager.py`, complete main controller | Full rerouting system working on Fat-tree |
| Day 5 | Implement `static_routing.py` + `simple_reactive.py`, run S1–S6 manually | Baseline systems working, first comparison data |
| Day 6 | Implement `experiment.py`, run automated 30-trial experiments on Fat-tree | First results CSV with confidence intervals |
| Day 7 | Add Internet2 + GÉANT topologies, run `ablation.py` and `pareto.py`, generate plots | Complete results for paper |

---

## Technology Stack Summary

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.10+ | All implementation |
| Ryu | 4.34 | SDN controller framework |
| Mininet | 2.3 | Network simulation |
| Open vSwitch | 2.17 | Software SDN switches |
| OpenFlow | 1.3 | Controller–switch protocol |
| NetworkX | 3.0+ | Graph + path computation |
| iperf3 | 3.x | Traffic generation |
| tcpreplay | 4.x | CAIDA/MAWI trace replay |
| Pandas | 2.0+ | Data collection and analysis |
| SciPy | 1.10+ | Confidence intervals, t-tests |
| Matplotlib | 3.7+ | Paper figures |
| PyYAML | 6.0+ | Configuration files |
