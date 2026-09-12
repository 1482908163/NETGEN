#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 节点资源完整小规模验证；配置集中在此，不必逐个模式手动提交。
# 6种策略 × 2种计时 × (1次预热 + 3次正式) = 48次运行。
# 不同进程规模仍并行提交、统一启动延时；同一作业内轮换策略顺序。
export EXPERIMENT_PRESET=cooperate
export EXPERIMENT_STAGE=kernel
export PROCESS_COUNTS="${PROCESS_COUNTS:-4}"
export RANKS_PER_NODE="${RANKS_PER_NODE:-4}"
export CPUS_PER_TASK="${CPUS_PER_TASK:-4}"
export KERNEL_THREAD_COUNTS="${KERNEL_THREAD_COUNTS:-4}"
export KERNEL_SCHEDULERS="${KERNEL_SCHEDULERS:-node_native node_scoped node_fixed node_lend node_guarded node_model}"
export ALGORITHMS="${ALGORITHMS:-sparse}"
export TIMING_MODES="${TIMING_MODES:-natural split}"
export REPEATS="${REPEATS:-3}"
export WARMUPS="${WARMUPS:-1}"
export CLEANUP_RESULTS="${CLEANUP_RESULTS:-1}"
export RESUME="${RESUME:-0}"
export START_DELAY_SECONDS="${START_DELAY_SECONDS:-30}"
export TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-600}"
export MPI_LAUNCHER="${SCRIPT_DIR}/node_affinity_yhrun.sh"

printf '节点资源验证：进程数=%s；每节点进程=%s；每进程预留核=%s\n' "${PROCESS_COUNTS}" "${RANKS_PER_NODE}" "${CPUS_PER_TASK}"
printf '策略=%s；计时=%s；预热=%s；正式重复=%s\n' "${KERNEL_SCHEDULERS}" "${TIMING_MODES}" "${WARMUPS}" "${REPEATS}"
printf '失败后继续；每次运行限时 %s 秒；结束后查看结果目录 p*/RESULT_SUMMARY.txt\n' "${TIMEOUT_SECONDS}"
exec "${SCRIPT_DIR}/run_experiments.sh" "$@"

