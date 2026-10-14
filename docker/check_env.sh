#!/bin/sh
# Phase 1 environment check. Run: .\docker\sdn.ps1 sh docker/check_env.sh
echo "--- versions"
ryu-manager --version 2>/dev/null
python3 -c 'import sys, mininet.net; print("mininet import OK, python", sys.version.split()[0])'
echo "mn $(mn --version 2>&1)"
ovs-vsctl --version | head -1
iperf3 --version | head -1
tcpreplay --version 2>&1 | head -1
hping3 --version 2>&1 | head -1
tshark --version 2>/dev/null | head -1
echo "--- ryu imports project code (python 3.9)"
/opt/ryu/bin/python -c 'import controller.detector, controller.path_engine, controller.scorer, controller.hysteresis; print("OK")'
echo "--- mn --test pingall"
mn --test pingall 2>&1 | grep -E 'Results|rror'
mn -c >/dev/null 2>&1
