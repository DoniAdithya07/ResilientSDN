"""WAN topologies read from Internet Topology Zoo GraphML files (CC BY 4.0,
github.com/mroughan/InternetTopologyZoo).

Any .graphml file dropped into topology/data/ becomes a topology named after the file
(lower case): `python -m topology.add Abilene` downloads one. Real graphs are messy, so the
loader repairs them: parallel links keep the fastest one, self-loops are dropped, duplicate or
missing node names get unique ones, nodes without a location are placed at the average of their
located neighbours, links without a speed get `default_link_gbps`, and only the largest
connected part is kept. Settings come from the topology's section of network_config.yaml if it
has one, otherwise from DEFAULTS.
"""
from pathlib import Path

import networkx as nx

from controller.config import PROJECT_ROOT, load_yaml
from topology.wan import WanTopology

DEFAULTS = {'max_link_mbps': 100, 'host_bw': 100, 'fibre_km_per_ms': 200, 'default_link_gbps': 10}
DATA_DIR = Path(__file__).resolve().parent / 'data'


def _coord(d):
    try:
        lat, lon = float(d['Latitude']), float(d['Longitude'])
    except (KeyError, TypeError, ValueError):
        return None
    return None if lat != lat or lon != lon else (lat, lon)   # NaN check


def read_zoo(path, cfg):
    """(nodes {name: (lat, lon)}, links [(a, b, gbps)]) from a Zoo GraphML file."""
    multi = nx.read_graphml(path)
    speed = {}   # fastest speed among parallel links between two nodes
    for u, v, d in multi.edges(data=True):
        if d.get('LinkSpeedRaw'):
            speed[frozenset((u, v))] = max(speed.get(frozenset((u, v)), 0.0), float(d['LinkSpeedRaw']))
    g = nx.Graph(multi)
    g.remove_edges_from(list(nx.selfloop_edges(g)))
    if g.number_of_edges() == 0:
        raise ValueError(f'{path}: no links')
    g = g.subgraph(max(nx.connected_components(g), key=len)).copy()

    def order(n):
        return (0, int(n)) if str(n).isdigit() else (1, str(n))
    ids = sorted(g.nodes, key=order)

    names, used = {}, set()
    for n in ids:
        label = str(g.nodes[n].get('label') or '').strip()
        if not label or label.lower() in ('none', 'nan'):
            label = f'node{n}'
        if label in used:
            label = f'{label}#{n}'
        used.add(label)
        names[n] = label

    coords = {n: _coord(g.nodes[n]) for n in ids}
    fixed = {k: tuple(v) for k, v in cfg.get('missing_coordinates', {}).items()}
    for n in ids:
        if coords[n] is None and names[n] in fixed:
            coords[n] = fixed[names[n]]
    known = [c for c in coords.values() if c]
    centre = (sum(c[0] for c in known) / len(known), sum(c[1] for c in known) / len(known)) if known else (0.0, 0.0)
    for n in ids:   # unlocated node: average of located neighbours, else the network centre
        if coords[n] is None:
            near = [coords[m] for m in g[n] if coords[m]]
            coords[n] = ((sum(c[0] for c in near) / len(near), sum(c[1] for c in near) / len(near))
                         if near else centre)

    links = []
    for u, v in g.edges:
        bps = speed.get(frozenset((u, v)), 0.0)
        links.append((names[u], names[v], bps / 1e9 if bps else cfg['default_link_gbps']))
    return {names[n]: coords[n] for n in ids}, links


def detour_hops(graph):
    """Longest shortest-detour around a single link (a bound for the controller's path search)."""
    worst = 2
    for u, v in graph.edges:
        h = graph.copy()
        h.remove_edge(u, v)
        if nx.has_path(h, u, v):
            worst = max(worst, nx.shortest_path_length(h, u, v))
    return worst


class ZooTopology(WanTopology):
    SECTION = None   # key in network_config.yaml (built-in Zoo topologies)
    FILE = None      # graphml file name inside topology/data/ (auto-discovered topologies)

    def __init__(self, config_path='config/network_config.yaml', name=None):
        section = self.SECTION or name
        cfg = dict(DEFAULTS)
        cfg.update(load_yaml(config_path).get(section) or {})
        path = PROJECT_ROOT / cfg['data'] if 'data' in cfg else DATA_DIR / (self.FILE or f'{name}.graphml')
        nodes, links = read_zoo(path, cfg)
        super().__init__(nodes, links, cfg)
        if not self.SECTION:   # added topologies: bound for the controller's candidate-path search
            self.max_path_hops = cfg.get('max_path_hops') or min(12, max(6, detour_hops(self.get_switch_graph()) + 2))
