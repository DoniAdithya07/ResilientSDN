#!/bin/sh
# Run a project command in WSL2 Ubuntu (Mininet needs root and Open vSwitch).
#   ./run.sh sh docker/check_env.sh
#   ./run.sh sh docker/connectivity_check.sh fat_tree
#   ./run.sh python3 -m evaluation.experiment --out results/x --topologies fat_tree --runs 5
#   ./run.sh python3 -m topology.run fat_tree        (Mininet CLI; start a controller first)
# Setup notes: docs/PROGRESS.md, "How to run (WSL2 Ubuntu)".
set -e
cd "$(dirname "$0")"
sudo modprobe openvswitch 2>/dev/null || true
sudo systemctl start openvswitch-switch
exec sudo env PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 PATH="$PATH" "$@"
