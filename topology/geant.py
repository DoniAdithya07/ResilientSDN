"""GÉANT European research network, 2012 (40 nodes, 61 links).
Nodes are national networks, named by country code. Data: topology/data/SOURCES.md"""
from topology.zoo import ZooTopology


class Geant(ZooTopology):
    SECTION = 'geant'
