#!/bin/sh
# Revision 2 experiments (Google Deep Review round 2), about 7-8 hours; resumable like run_full.sh.
#  1. fat-tree: ECMP-aware attacker (pairs cross the target under static AND ECMP routing):
#     static, static ECMP and ResilientSDN on S3, S4, S5, S11
#  2. backbones: DPP deadband applied to every candidate before the score (paired with Table V seeds)
#  3. backbones: leave-one-out without the capacity filter, incl. presaturation S9 (paired with Table V)
#  4. fat-tree: one-at-a-time moves with projected loads only (ablation A without the filter, same seeds)
set -e
cd "$(dirname "$0")"
mkdir -p results/r2_ecmp_attacker results/r2_gate_all results/r2_capacity_loo_wan results/r2_sequential
./run.sh python3 -m evaluation.experiment --out results/r2_ecmp_attacker --attacker ecmp --topologies fat_tree     --systems static static_ecmp resilient_sdn --scenarios S3 S4 S5 S11 --runs 5 | tee -a results/r2_ecmp_attacker/campaign.log
./run.sh python3 -m evaluation.experiment --out results/r2_gate_all --topologies nsfnet internet2 geant     --systems rsdn_gate_all --scenarios S3 S5 S9 S11 --runs 5 | tee -a results/r2_gate_all/campaign.log
./run.sh python3 -m evaluation.experiment --out results/r2_capacity_loo_wan --topologies nsfnet internet2 geant     --systems rsdn_no_capacity_filter --scenarios S3 S5 S9 S11 --runs 5 | tee -a results/r2_capacity_loo_wan/campaign.log
./run.sh python3 -m evaluation.experiment --out results/r2_sequential --topologies fat_tree     --systems rsdn_sequential_only --scenarios S3 S4 S5 S11 --runs 10 | tee -a results/r2_sequential/campaign.log
echo "Revision 2 experiments finished."
