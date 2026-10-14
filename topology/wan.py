"""Shared base for WAN topologies built from real node locations and links.

Switches are s1..sN and each has one host (h_i on s_i), because Linux limits
interface names to 15 characters ("Kansas City, MO-eth3" would not fit).
The real name of each node is kept in `labels`.
"""
import math

from topology.base_topology import BaseTopology

EARTH_RADIUS_KM = 6371.0


def great_circle_km(a, b):
    """Haversine distance between two (lat, lon) points in km."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


class WanTopology(BaseTopology):
    def __init__(self, nodes, links, cfg):
        """
        nodes: {real name: (lat, lon)}, in a fixed order
        links: [(real name a, real name b, gbps)]
        cfg:   the topology's section of network_config.yaml
        """
        super().__init__()
        self.cfg = cfg
        self.max_path_hops = cfg.get('max_path_hops')   # None = controller default
        self.switch_ids = {name: f's{i}' for i, name in enumerate(nodes, start=1)}
        self.labels = {sid: name for name, sid in self.switch_ids.items()}
        self.coords = {self.switch_ids[n]: c for n, c in nodes.items()}
        scale = cfg['max_link_mbps'] / max(g for _, _, g in links)
        self.backbone = []
        for a, b, gbps in links:
            sa, sb = self.switch_ids[a], self.switch_ids[b]
            km = great_circle_km(self.coords[sa], self.coords[sb])
            delay = round(km / cfg['fibre_km_per_ms'], 2)
            self.backbone.append((sa, sb, round(gbps * scale, 3), delay))

    def switches(self):
        return list(self.labels)

    def hosts(self):
        return [f'h{i}' for i in range(1, len(self.labels) + 1)]

    def links(self):
        host_links = [(f's{i}', f'h{i}', self.cfg['host_bw'], 0)
                      for i in range(1, len(self.labels) + 1)]
        return self.backbone + host_links
