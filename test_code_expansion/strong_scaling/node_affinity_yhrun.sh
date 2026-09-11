#!/usr/bin/env bash
set -Eeuo pipefail

# Validation-only MPI launcher: keep the Slurm allocation but disable per-rank
# CPU binding so NodeResources can partition the node CPU pool itself.
REAL_YHRUN="${REAL_YHRUN:-$(command -v yhrun)}"
[[ -n "${REAL_YHRUN}" && -x "${REAL_YHRUN}" ]] || {
    echo "Cannot locate real yhrun" >&2
    exit 2
}

: "${PROCESS_COUNT:?}"
: "${RANKS_PER_NODE:?}"
: "${CPUS_PER_TASK:?}"

nodes=$(( (PROCESS_COUNT + RANKS_PER_NODE - 1) / RANKS_PER_NODE ))

# run_experiments.sh already passes -n PROCESS_COUNT in "$@". Do not add a
# second -n here. Extract --profile-dir so failures always leave a small,
# Git-friendly diagnostic text file in the run directory.
profile_dir=""
args=("$@")
for ((i=0;i<${#args[@]};++i)); do
    if [[ "${args[$i]}" == "--profile-dir" && $((i+1)) -lt ${#args[@]} ]]; then
        profile_dir="${args[$((i+1))]}"
        break
    fi
done

stderr_copy=""
if [[ -n "${profile_dir}" ]]; then
    mkdir -p "${profile_dir}"
    stderr_copy="${profile_dir}/node_coop_stderr.txt"
fi

set +e
if [[ -n "${stderr_copy}" ]]; then
    "${REAL_YHRUN}" \
        -N "${nodes}" \
        --ntasks-per-node "${RANKS_PER_NODE}" \
        --distribution block \
        --cpus-per-task "${CPUS_PER_TASK}" \
        --cpu-bind none \
        "$@" 2> >(tee "${stderr_copy}" >&2)
    rc=$?
else
    "${REAL_YHRUN}" \
        -N "${nodes}" \
        --ntasks-per-node "${RANKS_PER_NODE}" \
        --distribution block \
        --cpus-per-task "${CPUS_PER_TASK}" \
        --cpu-bind none \
        "$@"
    rc=$?
fi
set -e

if [[ -n "${stderr_copy}" && -f "${stderr_copy}" ]]; then
    grep -E 'node_coop_v1:|Abort\(87\)|MPI|sched_setaffinity|affinity' "${stderr_copy}" \
        > "${profile_dir}/node_coop_errors.txt" || true
fi
exit "${rc}"
