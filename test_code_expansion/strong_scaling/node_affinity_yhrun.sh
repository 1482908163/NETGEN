#!/usr/bin/env bash
set -Eeuo pipefail

# Validation-only MPI launcher: keep the Slurm allocation (4 ranks/node, 4 CPUs/rank)
# but disable per-rank CPU binding so NodeResources can partition the node CPU pool.
REAL_YHRUN="${REAL_YHRUN:-$(command -v yhrun)}"
[[ -n "${REAL_YHRUN}" && -x "${REAL_YHRUN}" ]] || {
    echo "Cannot locate real yhrun" >&2
    exit 2
}

: "${PROCESS_COUNT:?}"
: "${RANKS_PER_NODE:?}"
: "${CPUS_PER_TASK:?}"

nodes=$(( (PROCESS_COUNT + RANKS_PER_NODE - 1) / RANKS_PER_NODE ))
exec "${REAL_YHRUN}" \
    -N "${nodes}" \
    --ntasks-per-node "${RANKS_PER_NODE}" \
    --distribution block \
    --cpus-per-task "${CPUS_PER_TASK}" \
    --cpu-bind none \
    "$@"
