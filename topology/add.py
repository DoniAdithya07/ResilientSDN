"""Add a topology from the Internet Topology Zoo (or your own GraphML file).

    .venv\Scripts\python -m topology.add Abilene            # download graphml/Abilene.graphml
    .venv\Scripts\python -m topology.add path\to\my.graphml  # copy a local file

It is saved in topology/data/, checked by loading it, and can then be used by name (lower case) in
every command, for example:  ./run.sh python3 -m evaluation.experiment --topologies abilene --runs 5
Zoo names: github.com/mroughan/InternetTopologyZoo/tree/main/graphml
"""
import shutil
import sys
import urllib.request
from pathlib import Path

from topology import DATA_DIR

URL = 'https://raw.githubusercontent.com/mroughan/InternetTopologyZoo/main/graphml/{}.graphml'


def add(source):
    src = Path(source)
    if src.suffix == '.graphml' and src.exists():
        name, dest = src.stem, DATA_DIR / src.name
        if src.resolve() != dest.resolve():
            shutil.copy(src, dest)
    else:
        name, dest = source, DATA_DIR / f'{source}.graphml'
        with urllib.request.urlopen(URL.format(source), timeout=30) as r:
            dest.write_bytes(r.read())
    from topology.zoo import ZooTopology
    topo = ZooTopology(name=name.lower())
    g = topo.get_switch_graph()
    print(f'{name.lower()}: {g.number_of_nodes()} switches, {g.number_of_edges()} links, saved to {dest}')
    print(f'use it with:  --topologies {name.lower()}   (controller path limit {topo.max_path_hops} hops)')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    add(sys.argv[1])
