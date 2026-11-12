#!/bin/sh
# Revision 1 experiments (Google Deep Review round 1), about 10 hours; resumable like run_full.sh.
#  1. backbones: score ablation (shortest alternate), normalised drift and relief bypass (candidate fixes)
#  2. fat-tree: leave-one-out without the capacity filter (with the full system, 10 runs, same seeds)
#  3. fat-tree: static ECMP baseline, all scenarios
#  4. fat-tree: relief bypass on S3, S8, S11 (does the fix cost stability on the fat-tree?)
set -e
cd "$(dirname "$0")"
mkdir -p results/r1_wan_variants results/r1_capacity_loo results/r1_ecmp results/r1_relief_fat
./run.sh python3 -m evaluation.experiment --out results/r1_wan_variants --topologies nsfnet internet2 geant \
    --systems rsdn_shortest rsdn_norm_drift rsdn_relief_bypass --scenarios S3 S5 S9 S11 --runs 5 | tee -a results/r1_wan_variants/campaign.log
./run.sh python3 -m evaluation.experiment --out results/r1_capacity_loo --topologies fat_tree \
    --systems rsdn_no_capacity_filter resilient_sdn --scenarios S3 S5 S11 --runs 10 | tee -a results/r1_capacity_loo/campaign.log
./run.sh python3 -m evaluation.experiment --out results/r1_ecmp --topologies fat_tree \
    --systems static_ecmp --runs 5 | tee -a results/r1_ecmp/campaign.log
./run.sh python3 -m evaluation.experiment --out results/r1_relief_fat --topologies fat_tree \
    --systems rsdn_relief_bypass --scenarios S3 S8 S11 --runs 5 | tee -a results/r1_relief_fat/campaign.log
echo "Revision 1 experiments finished."
