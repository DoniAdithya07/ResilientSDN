#!/bin/sh
# Day 1 check: Ryu (static routing) + Fat-tree, ping every host pair.
# Run: .\docker\sdn.ps1 sh docker/connectivity_check.sh [topology]
TOPO=${1:-fat_tree}
LOG=.local/work/ryu_connectivity.log
mkdir -p .local/work
RESILIENT_TOPO=$TOPO ryu-manager --ofp-listen-host 127.0.0.1 baselines/static_routing.py > "$LOG" 2>&1 &
RYU=$!
sleep 3
python3 -m topology.run "$TOPO" --pingall
STATUS=$?
kill $RYU 2>/dev/null
mn -c >/dev/null 2>&1
echo "--- ryu errors:"
grep -iE "error|traceback|exception" "$LOG" || echo "none"
exit $STATUS
