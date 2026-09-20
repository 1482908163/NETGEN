#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 自然模式低扰动同步诊断：只比较固定资源与当前主方案 node_elastic。
# 只测 128/256 进程，不插入 split barrier；用自然时间戳离线计算 collective arrival skew。
# 每规模 2组 × (1次预热 + 5次正式) = 12次，共24次；预热含质量审计。
export EXPERIMENT_PRESET=cooperate
export EXPERIMENT_STAGE=kernel
export PROCESS_COUNTS="${PROCESS_COUNTS:-128 256}"
export RANKS_PER_NODE="${RANKS_PER_NODE:-4}"
export CPUS_PER_TASK="${CPUS_PER_TASK:-4}"
export KERNEL_THREAD_COUNTS="${KERNEL_THREAD_COUNTS:-4}"
export QUALITY_WARMUP="${QUALITY_WARMUP:-1}"
export COMMUNICATION_ABLATION="${COMMUNICATION_ABLATION:-0}"
export KERNEL_SCHEDULERS="${KERNEL_SCHEDULERS:-node_fixed node_elastic}"
export LEVELS="${LEVELS:-2}"
export REFINES="${REFINES:-1}"
export ALGORITHMS="${ALGORITHMS:-sparse}"
export TIMING_MODES="${TIMING_MODES:-natural}"
export REPEATS="${REPEATS:-5}"
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

