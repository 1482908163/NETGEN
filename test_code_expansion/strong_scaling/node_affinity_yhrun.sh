#!/usr/bin/env bash
set -Eeuo pipefail

# 协作模式由 NodeResources 划分共享核池；原修复基准由 Slurm 固定绑核。
# 所有策略使用同样的节点、进程数及每进程预留核数。
REAL_YHRUN="${REAL_YHRUN:-$(command -v yhrun)}"
[[ -n "${REAL_YHRUN}" && -x "${REAL_YHRUN}" ]] || {
    echo "Cannot locate real yhrun" >&2
    exit 2
}

: "${PROCESS_COUNT:?}"
: "${RANKS_PER_NODE:?}"
: "${CPUS_PER_TASK:?}"

nodes=$(( (PROCESS_COUNT + RANKS_PER_NODE - 1) / RANKS_PER_NODE ))
cpu_bind=cores
case "${KERNEL_SCHEDULER:-static}" in
    node_window|node_window_priority|node_original|node_native|node_scoped|node_fixed|node_lend|node_guarded|node_model|node_tail|node_budget|node_priority|node_reserved|node_elastic|node_stage|node_reclaim|node_selective|node_once) cpu_bind=none ;;
esac

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
    exec {stderr_fd}> >(tee "${stderr_copy}" >&2)
    stderr_pid=$!
    "${REAL_YHRUN}" \
        -N "${nodes}" \
        --ntasks-per-node "${RANKS_PER_NODE}" \
        --distribution block \
        --cpus-per-task "${CPUS_PER_TASK}" \
        --cpu-bind "${cpu_bind}" \
        "$@" 2>&"${stderr_fd}"
    rc=$?
    exec {stderr_fd}>&-
    # 等待 tee 写完诊断，再生成摘要，避免短进程退出时丢失末尾信息。
    wait "${stderr_pid}" || true
else
    "${REAL_YHRUN}" \
        -N "${nodes}" \
        --ntasks-per-node "${RANKS_PER_NODE}" \
        --distribution block \
        --cpus-per-task "${CPUS_PER_TASK}" \
        --cpu-bind "${cpu_bind}" \
        "$@"
    rc=$?
fi
set -e

if [[ -n "${stderr_copy}" && -f "${stderr_copy}" ]]; then
    # 正常初始化也写 stderr，不能把成功信息当错误。
    grep -E 'node_coop_v1:|node_coop_v2_layout:|Abort\(87\)|MPI|sched_setaffinity|affinity' "${stderr_copy}" \
        > "${profile_dir}/node_coop_diagnostics.txt" || true
    grep -Ev '^node_coop_v1: node_group=.* shared_state=posix_shm affinity_layout=|^node_coop_v2_layout: leader_world_rank=' \
        "${profile_dir}/node_coop_diagnostics.txt" > "${profile_dir}/node_coop_errors.txt" || true
fi
exit "${rc}"


