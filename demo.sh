#!/bin/sh
# Live demo (about 6 minutes): fat-tree, no attack (S1) and one attack (S3), 3 systems, 1 run each.
# Real emulation with the same code as the full evaluation. Builds results/demo/report.pdf.
# Do not run it while the full campaign is running (both need the same Linux kernel).
#   WSL:  cd "/mnt/d/sdn project  and assingmensts/project" && ./demo.sh
set -e
cd "$(dirname "$0")"
rm -rf results/demo
./run.sh python3 -m evaluation.experiment --config config/smoke_config.yaml --out results/demo \
    --topologies fat_tree --scenarios S1 S3 --runs 1
echo "Demo finished. Open results/demo/report.pdf and check the 'Correctness checks' table: every line must say PASS."
