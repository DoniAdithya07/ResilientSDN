from pathlib import Path

BUILTIN = ('fat_tree', 'internet2', 'geant', 'nsfnet')
DATA_DIR = Path(__file__).resolve().parent / 'data'
_BUILTIN_FILES = {'geant2012', 'nsfnet'}   # graphml files already used by geant / nsfnet


def discovered():
    """Extra topologies: every other .graphml file in topology/data/, named after the file (lower case)."""
    return sorted(p.stem.lower() for p in DATA_DIR.glob('*.graphml') if p.stem.lower() not in _BUILTIN_FILES)


TOPOLOGIES = BUILTIN + tuple(discovered())


def load_topology(name):
    """Topology object by name, as used in experiment_config.yaml."""
    if name == 'fat_tree':
        from topology.fat_tree import FatTree
        return FatTree()
    if name == 'internet2':
        from topology.internet2 import Internet2
        return Internet2()
    if name == 'geant':
        from topology.geant import Geant
        return Geant()
    if name == 'nsfnet':
        from topology.nsfnet import Nsfnet
        return Nsfnet()
    if name in discovered():
        from topology.zoo import ZooTopology
        return ZooTopology(name=name)
    raise ValueError(f'unknown topology: {name} (valid: {", ".join(TOPOLOGIES)})')
