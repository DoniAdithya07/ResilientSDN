#!/bin/sh
# The full evaluation, one part after another (about 17-19 hours in total; see docs/HOW_TO_RUN.md).
# Safe to stop and start again: finished runs are skipped. At the end it builds the final PDF.
#   WSL:  cd "/mnt/d/sdn project  and assingmensts/project" && ./run_full.sh
# Another topology: python -m topology.add <ZooName> (Windows), then add it to --topologies below.
cd "$(dirname "$0")"
mkdir -p results/fattree_5runs results/wan_5runs
./run.sh python3 -m evaluation.experiment --out results/fattree_5runs --topologies fat_tree --runs 5 | tee -a results/fattree_5runs/campaign.log
./run.sh python3 -m evaluation.experiment --out results/wan_5runs --topologies nsfnet internet2 geant \
    --scenarios S1 S3 S5 S9 S11 --runs 5 | tee -a results/wan_5runs/campaign.log
./run.sh python3 -m evaluation.ablation A B C D --topology fat_tree --runs 10
echo "All parts finished. Each results folder has a report.pdf; building the final PDF..."
if [ -x .venv/Scripts/python.exe ]; then
    .venv/Scripts/python.exe -W ignore -m analysis.final_report
else
    echo 'On Windows run:  .venv\Scripts\python -W ignore -m analysis.final_report'
fi
