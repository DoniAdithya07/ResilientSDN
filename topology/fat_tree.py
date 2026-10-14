"""Fat-tree k=4 datacenter topology (20 switches, 16 hosts).

    Core switches:  (k/2)² = 4      c1..c4
    Aggregation:    k*(k/2) = 8     a1..a8   (pod p owns a{p*k/2+1}..)
    Edge switches:  k*(k/2) = 8     e1..e8
    Hosts per edge: k/2 = 2         h1..h16
"""
from controller.config import load_yaml
from topology.base_topology import BaseTopology


class FatTree(BaseTopology):
    def __init__(self, k=None, config_path='config/network_config.yaml'):
        super().__init__()
        cfg = load_yaml(config_path)['fat_tree']
        self.k = k or cfg['k']
        if self.k % 2:
            raise ValueError('k must be even')
        self.cfg = cfg
        half = self.k // 2
        self.core = [f'c{i + 1}' for i in range(half * half)]
        self.agg = [f'a{i + 1}' for i in range(self.k * half)]
        self.edge = [f'e{i + 1}' for i in range(self.k * half)]
        self.host_names = [f'h{i + 1}' for i in range(self.k * half * half)]

    def switches(self):
        return self.core + self.agg + self.edge

    def hosts(self):
        return self.host_names

    def links(self):
        k, half, d = self.k, self.k // 2, self.cfg['delay_ms']
        out = []
        for pod in range(k):
            pod_agg = self.agg[pod * half:(pod + 1) * half]
            pod_edge = self.edge[pod * half:(pod + 1) * half]
            # Aggregation switch i in each pod connects to core group i
            for i, a in enumerate(pod_agg):
                for c in self.core[i * half:(i + 1) * half]:
                    out.append((c, a, self.cfg['core_agg_bw'], d))
            # Full mesh between a pod's aggregation and edge layers
            for a in pod_agg:
                for e in pod_edge:
                    out.append((a, e, self.cfg['agg_edge_bw'], d))
            # Hosts under each edge switch
            for j, e in enumerate(pod_edge):
                first = (pod * half + j) * half
                for h in self.host_names[first:first + half]:
                    out.append((e, h, self.cfg['edge_host_bw'], d))
        return out
