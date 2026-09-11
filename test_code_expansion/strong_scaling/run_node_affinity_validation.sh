#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Minimal admission test for node-local CPU-pool management.
# Keep this validation isolated from the production cooperate preset.
export EXPERIMENT_PRESET=cooperate
export PROCESS_COUNTS="${PROCESS_COUNTS:-4}"
export RANKS_PER_NODE="${RANKS_PER_NODE:-4}"
export CPUS_PER_TASK="${CPUS_PER_TASK:-4}"
export KERNEL_THREAD_COUNTS="${KERNEL_THREAD_COUNTS:-4}"
export KERNEL_SCHEDULERS="${KERNEL_SCHEDULERS:-node_fixed}"
export ALGORITHMS="${ALGORITHMS:-sparse}"
export TIMING_MODES="${TIMING_MODES:-natural}"
export REPEATS="${REPEATS:-1}"
export WARMUPS="${WARMUPS:-0}"
export CLEANUP_RESULTS="${CLEANUP_RESULTS:-0}"
export RESUME="${RESUME:-0}"
export START_DELAY_SECONDS="${START_DELAY_SECONDS:-30}"
export MPI_LAUNCHER="${SCRIPT_DIR}/node_affinity_yhrun.sh"

exec "${SCRIPT_DIR}/run_experiments.sh" "$@"
