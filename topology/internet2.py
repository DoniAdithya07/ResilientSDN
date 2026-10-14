"""Internet2 OS3E WAN topology (34 nodes, 42 links, 100 Gbps backbone).
Data and sources: topology/data/internet2_os3e.yaml"""
from controller.config import load_yaml
from topology.wan import WanTopology


class Internet2(WanTopology):
    def __init__(self, config_path='config/network_config.yaml'):
        cfg = load_yaml(config_path)['internet2']
        data = load_yaml(cfg['data'])
        nodes = {name: tuple(c) for name, c in data['nodes'].items()}
        super().__init__(nodes, [tuple(l) for l in data['links']], cfg)
