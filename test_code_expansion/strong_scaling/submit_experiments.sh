#!/usr/bin/env bash
set -Eeuo pipefail
invoked_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="${STRONG_SCALING_DIR:-${invoked_script_dir}}"
SCRIPT_DIR="$(cd "${SCRIPT_DIR}" && pwd)"
export STRONG_SCALING_DIR="${SCRIPT_DIR}"

# Compatibility: direct calls enter the same unified configuration first.
if [[ "${MESH_EXPERIMENT_DRIVER_READY:-0}" != 1 ]]; then
    exec "${SCRIPT_DIR}/run_experiments.sh" "$@"
fi

REPOSITORY_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# Optional label supplied by the user; experiment scripts do not invoke Git.
export MESH_SOURCE_REVISION="${MESH_SOURCE_REVISION:-manual}"
export RUN_ROOT="${RUN_ROOT:-${REPOSITORY_ROOT}/strong_scaling_results/mesh_algorithms_$(date +%Y%m%d-%H%M%S)}"
export START_EPOCH="${START_EPOCH:-$(( $(date +%s)+START_DELAY_SECONDS ))}"
# 一次提交全部计划；每个作业独立预热验证，不建立作业间串行依赖。
if [[ "${RULE_SEARCH_ALL:-0}" == 1 ]]; then
    export RULE_SEARCH_BATCH_ROOT="${RUN_ROOT}"
    python3 "${SCRIPT_DIR}/rule_search_batch.py" "${RUN_ROOT}" --initialize
    failures=0
    for group in precheck main; do
        if [[ "$group" == precheck ]]; then
            levels="$RULE_SEARCH_CHECK_LEVEL";nodes="$RULE_SEARCH_CHECK_NODES";repeats="$RULE_SEARCH_CHECK_REPEATS"
        else
            levels="$RULE_SEARCH_MAIN_LEVELS";nodes="$RULE_SEARCH_MAIN_NODES";repeats="$RULE_SEARCH_MAIN_REPEATS"
        fi
        counts=""
        for node_count in ${nodes//,/ }; do counts+="$((node_count*4)) "; done
        if ! RULE_SEARCH_ALL=0 EXPERIMENT_PRESET=rule_search RULE_SEARCH_SWEEP=1 \
            RULE_SEARCH_LEVELS="$levels" PROCESS_COUNTS="$counts" REPEATS="$repeats" \
            RULE_SEARCH_BATCH_GROUP="$group" RUN_ROOT="${RULE_SEARCH_BATCH_ROOT}/${group}" \
            bash "${SCRIPT_DIR}/run_experiments.sh" "$@"; then
            failures=$((failures+1))
        fi
    done
    python3 "${SCRIPT_DIR}/rule_search_batch.py" "${RULE_SEARCH_BATCH_ROOT}"
    printf '本轮全部实验已处理；统一查看：%s/RESULT_SUMMARY.txt\n' "${RULE_SEARCH_BATCH_ROOT}"
    ((failures==0)) || exit 1
    exit 0
fi
# Rule-search runs retain the same independent submission and shared start time.
# They do not reuse the older three-route borrowing analyzer.
if [[ "${RULE_SEARCH_SWEEP:-0}" == 1 ]]; then
    failures=0
    for level in ${RULE_SEARCH_LEVELS//,/ }; do
        [[ "$level" =~ ^[0-9]+$ ]] || { echo "非法面细化级数" >&2; exit 2; }
        if ! RULE_SEARCH_SWEEP=0 LEVELS="$level" RUN_ROOT="${RUN_ROOT}/levels${level}" \
            bash "${SCRIPT_DIR}/submit_experiments.sh" "$@"; then
            failures=$((failures+1))
        fi
    done
    printf '规则搜索结果：%s/levels*/p*/RESULT_SUMMARY.txt\n' "${RUN_ROOT}"
    ((failures==0)) || exit 1
    exit 0
fi
# 不同工作量作为独立作业同时提交，共享最早启动时刻，结果按细化级数分开。
if [[ "${WORKLOAD_SWEEP:-0}" == 1 ]]; then
    export WORKLOAD_ROOT="${RUN_ROOT}"
    python3 "${SCRIPT_DIR}/analyze_node_scaling.py" "${WORKLOAD_ROOT}" --init-workload
    failures=0
    for level in ${WORKLOAD_LEVELS//,/ }; do
        if ! WORKLOAD_SWEEP=0 LEVELS="${level}" RUN_ROOT="${WORKLOAD_ROOT}/levels${level}" \
            bash "${SCRIPT_DIR}/submit_experiments.sh" "$@"; then
            failures=$((failures+1))
            mkdir -p "${WORKLOAD_ROOT}/levels${level}"
            printf '提交失败\n' > "${WORKLOAD_ROOT}/levels${level}/submission_failed.txt"
        fi
    done
    python3 "${SCRIPT_DIR}/analyze_node_scaling.py" "${WORKLOAD_ROOT}" --workload-summary
    printf '整批结果: %s\n' "${WORKLOAD_ROOT}"
    ((failures==0)) || exit 1
    exit 0
fi
export MESH_EXPERIMENT_WORKER=1
[[ "${RANKS_PER_NODE}" =~ ^[1-9][0-9]*$ && "${START_EPOCH}" =~ ^[0-9]+$ ]] || exit 2
read -r -a counts <<< "${PROCESS_COUNTS//,/ }"
read -r -a batch_extra <<< "${SBATCH_EXTRA_ARGS:-}"
# 所有协作对照采用相同物理核布局，避免把超线程当额外计算核。
[[ "${EXPERIMENT_PRESET}" != cooperate ]] || batch_extra+=(--hint=nomultithread)
# Unbound rank affinity must not let two independent jobs select the same pool.
[[ "${NODE_SCALING:-0}" != 1 ]] || batch_extra+=(--exclusive)
((${#counts[@]}>0)) || exit 2
for p in "${counts[@]}"; do [[ "${p}" =~ ^[1-9][0-9]*$ ]] || exit 2; done
mkdir -p "${RUN_ROOT}"
if [[ "${BALANCE_METHOD}" == task_queue ]]; then
    for p in "${counts[@]}"; do
        (( p>=2 && TASK_COUNT>=p-1 )) || { echo "任务数须不少于进程数减一，且至少两进程。" >&2;exit 2; }
    done
fi
if [[ "${EXPERIMENT_STAGE}" == evaluation && "${BALANCE_METHOD}" != task_queue ]]; then
    [[ -n "${CALIBRATION_ROOT}" && -d "${CALIBRATION_ROOT}" ]] || {
        echo "Set CALIBRATION_ROOT in run_experiments.sh to the completed v3 multi-seed calibration directory." >&2
        exit 2
    }
    if [[ "${BALANCE_METHOD}" == node_mapping ]]; then
        [[ "${PARTITION_SEEDS}" == 41 && "${PARTITION_VARIANT}" == cell_order_v1 ]] || {
            echo "本轮映射评价使用留出分区 41。" >&2;exit 2;
        }
        python3 "${SCRIPT_DIR}/resource_model.py" --train "${CALIBRATION_ROOT}" --output "${RUN_ROOT}/models" \
            --target-ranks "${counts[@]}" --levels "${LEVELS}" --refines "${REFINES}" --rpn "${RANKS_PER_NODE}"
    else
        python3 "${SCRIPT_DIR}/fit_cost_model.py" --calibration-root "${CALIBRATION_ROOT}" \
            --target-ranks "${counts[@]}" --levels "${LEVELS}" --refines "${REFINES}" --output "${RUN_ROOT}/models" --require-v3 --require-sampling
    fi
fi
if [[ "${NODE_SCALING:-0}" == 1 ]]; then
    python3 "${SCRIPT_DIR}/analyze_node_scaling.py" "${RUN_ROOT}" --init
fi
printf '%s\n' "${counts[@]}" > "${RUN_ROOT}/requested_process_counts.txt"
failures=0
for p in "${counts[@]}"; do
    nodes=$(( (p+RANKS_PER_NODE-1)/RANKS_PER_NODE ))
    mkdir -p "${RUN_ROOT}/p${p}"
    export PROCESS_COUNT="${p}"
    job_name="mesh_p${p}"
    [[ -z "${WORKLOAD_ROOT:-}" && "${RULE_SEARCH_EXPERIMENT:-0}" != 1 ]] || job_name="mesh_l${LEVELS}_p${p}"
    command=("${SBATCH_COMMAND}" --parsable -p "${PARTITION}" -N "${nodes}" -n "${p}"
             --cpus-per-task "${CPUS_PER_TASK}" --ntasks-per-node "${RANKS_PER_NODE}" --export=ALL --job-name "${job_name}"
             --output "${RUN_ROOT}/p${p}/job-%j.log" "${batch_extra[@]}" "${SCRIPT_DIR}/run_experiments.sh")
    if [[ "${DRY_RUN}" == 1 ]]; then
        printf '%q ' "${command[@]}"; printf '\n'
    else
        if jobid=$("${command[@]}"); then
            printf '%s\t%s\n' "${p}" "${jobid}" >> "${RUN_ROOT}/submitted_jobs.tsv"
        else
            if [[ "${NODE_SCALING:-0}" == 1 ]]; then
                python3 "${SCRIPT_DIR}/analyze_node_scaling.py" "${RUN_ROOT}" --mark-ranks "$p" --exit-code 125 || true
            fi
            if [[ -n "${RULE_SEARCH_BATCH_ROOT:-}" ]]; then
                python3 "${SCRIPT_DIR}/rule_search_batch.py" "${RULE_SEARCH_BATCH_ROOT}" \
                    --mark "${RULE_SEARCH_BATCH_GROUP}/levels${LEVELS}/p${p}" --exit-code 125
            fi
            failures=$((failures+1));printf '%s\n' "${p}" >> "${RUN_ROOT}/failed_submissions.txt"
        fi
    fi
done
printf 'Results: %s\n' "${RUN_ROOT}"
((failures==0)) || exit 1

