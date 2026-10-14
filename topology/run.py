"""Start a topology against a remote Ryu controller (run inside the container).

    python3 -m topology.run fat_tree            # Mininet CLI
    python3 -m topology.run fat_tree --pingall  # ping all host pairs, then exit
"""
import argparse
import sys

from mininet.cli import CLI
from mininet.log import setLogLevel

from topology import load_topology


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('topology')
    parser.add_argument('--pingall', action='store_true')
    args = parser.parse_args()

    setLogLevel('info')
    topo = load_topology(args.topology)
    topo.start()
    try:
        topo.net.waitConnected()
        if args.pingall:
            loss = topo.net.pingAll()
            return 0 if loss == 0 else 1
        CLI(topo.net)
        return 0
    finally:
        topo.stop()


if __name__ == '__main__':
    sys.exit(main())
