"""NSFNET T3 backbone, 1991 (13 nodes, 15 links), downloaded from the Internet
Topology Zoo. Data and gaps filled: topology/data/SOURCES.md"""
from topology.zoo import ZooTopology


class Nsfnet(ZooTopology):
    SECTION = 'nsfnet'
