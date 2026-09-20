#!/usr/bin/env bash
set -Eeuo pipefail
# Slurm/yhbatch executes a spool copy such as /tmp/slurmd/job*/slurm_script.
# The submitter exports the original directory so worker jobs can find the
# companion environment and analysis scripts beside the repository source.
invoked_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="${STRONG_SCALING_DIR:-${invoked_script_dir}}"
SCRIPT_DIR="$(cd "${SCRIPT_DIR}" && pwd)"
export STRONG_SCALING_DIR="${SCRIPT_DIR}"

# ============================================================================
# 统一实验配置区：通常只需修改 EXPERIMENT_PRESET，然后直接运行本脚本。
#   cooperate  : 1/4/16/32/64 节点，每节点4进程/16核，完成贡献消融与扩展验证
#   kernel     : 1/4/16 节点、每节点1进程/16核，比较修复策略与同步等待
#   pilot      : 1/2/4 节点，小规模正确性与流程预检
#   production : 64/128/256/512 节点，复现正式大规模实验配置
# 环境变量仍可覆盖这些默认值，主要供作业脚本内部传递及断点续跑使用。
# ============================================================================
EXPERIMENT_PRESET="${EXPERIMENT_PRESET:-cooperate}"
# 默认保持一进程一分区和稀疏通信，比较节点内资源分配。
# boundary / node_mapping / task_queue 仅供显式复现历史失败方案。
default_stage=communication
[[ "${EXPERIMENT_PRESET}" != kernel && "${EXPERIMENT_PRESET}" != cooperate ]] || default_stage=kernel
EXPERIMENT_STAGE="${EXPERIMENT_STAGE:-${default_stage}}"
# 内核原型：同一进程数/分区，固定预留核数，比较内部调度和线程数。
default_kernel_counts="4 16";default_kernel_schedulers="static repair"
[[ "${EXPERIMENT_PRESET}" != cooperate ]] || { default_kernel_counts=4;default_kernel_schedulers="node_original node_native node_fixed node_elastic"; }
KERNEL_THREAD_COUNTS="${KERNEL_THREAD_COUNTS:-${default_kernel_counts}}"
# 已验证基线：sparse + repair；static 留作消融，frontier 仅显式复现失败方案。
KERNEL_SCHEDULERS="${KERNEL_SCHEDULERS:-${default_kernel_schedulers}}"
KERNEL_THREADS="${KERNEL_THREADS:-0}"
KERNEL_SCHEDULER="${KERNEL_SCHEDULER:-static}"
default_cpus=1;default_rpn=16
[[ "${EXPERIMENT_PRESET}" != kernel ]] || { default_cpus=16;default_rpn=1; }
[[ "${EXPERIMENT_PRESET}" != cooperate ]] || { default_cpus=4;default_rpn=4; }
CPUS_PER_TASK="${CPUS_PER_TASK:-${default_cpus}}"
export CPUS_PER_TASK KERNEL_THREADS KERNEL_SCHEDULER KERNEL_THREAD_COUNTS KERNEL_SCHEDULERS
BALANCE_METHOD="${BALANCE_METHOD:-none}"
CALIBRATION_ROOT="${CALIBRATION_ROOT:-${SCRIPT_DIR}/../strong_scaling_results/mesh_algorithms_20260908-135236}"
MIN_GAIN_SECONDS="${MIN_GAIN_SECONDS:-0.35}"
MIN_GAIN_FRACTION="${MIN_GAIN_FRACTION:-0.05}"
CLOSURE_GROWTH="${CLOSURE_GROWTH:-0}"
SEARCH_SECONDS="${SEARCH_SECONDS:-0.35}"
case "${EXPERIMENT_STAGE}" in
    communication|kernel)
        default_algorithms="baseline sparse"
        default_timings="natural split"
        default_seeds="-1"
        default_repeats=5
        ;;
    calibration)
        default_algorithms="sparse"
        default_timings="natural"
        default_seeds="-1 17 41"
        default_repeats=3
        ;;
    evaluation|legacy)
        default_algorithms="baseline balance sparse combined"
        default_timings="natural split"
        default_seeds="-1"
        [[ "${EXPERIMENT_STAGE}" == evaluation && "${BALANCE_METHOD}" != boundary ]] && default_seeds="41"
        default_repeats=5
        ;;
    *) echo "EXPERIMENT_STAGE must be kernel, communication, calibration, evaluation or legacy" >&2; exit 2 ;;
esac
case "${EXPERIMENT_PRESET}" in
    kernel|cooperate)
        default_tasks=0
        default_process_counts="1 4 16"
        [[ "${EXPERIMENT_PRESET}" != cooperate ]] || default_process_counts="4 16 64 128 256"
        [[ "${EXPERIMENT_PRESET}" != cooperate ]] || default_repeats=5
        default_levels=1
        [[ "${EXPERIMENT_PRESET}" != cooperate ]] || default_levels=2
        default_refines=1
        default_verify_faces=0
        default_algorithms="sparse"
        default_timings="natural split"
        ;;
    pilot)
        default_tasks=128
        default_process_counts="16 32 64"
        default_levels=1
        default_refines=1
        default_verify_faces=1
        ;;
    production)
        default_tasks=16384
        default_process_counts="1024 2048 4096 8192"
        default_levels=3
        default_refines=3
        default_verify_faces=0
        ;;
    *) echo "EXPERIMENT_PRESET must be cooperate, kernel, pilot or production" >&2; exit 2 ;;
esac

TASK_COUNT="${TASK_COUNT:-${default_tasks}}"  # 仅历史 task_queue 使用；通信基线忽略。
TASK_CUT_GROWTH="${TASK_CUT_GROWTH:-0.10}"  # 跨节点粗面切分相对固定分配最多增加 10%。
PROCESS_COUNTS="${PROCESS_COUNTS:-${default_process_counts}}"
ALGORITHMS="${ALGORITHMS:-${default_algorithms}}"
TIMING_MODES="${TIMING_MODES:-${default_timings}}"
# -1 保留原输入顺序；17/41 用于固定粗单元重排，并检查实际归属确实不同。
PARTITION_SEEDS="${PARTITION_SEEDS:-${default_seeds}}"
default_variant=cell_order_v1
[[ "${EXPERIMENT_STAGE}" == legacy || "${EXPERIMENT_STAGE}" == communication || "${EXPERIMENT_STAGE}" == kernel ]] && default_variant=metis_seed
PARTITION_VARIANT="${PARTITION_VARIANT:-${default_variant}}"
PLACEMENT_ROTATION="${PLACEMENT_ROTATION:-1}"
RANKS_PER_NODE="${RANKS_PER_NODE:-${default_rpn}}"
REPEATS="${REPEATS:-${default_repeats}}"
WARMUPS="${WARMUPS:-1}"
LEVELS="${LEVELS:-${default_levels}}"
REFINES="${REFINES:-${default_refines}}"
MAXH="${MAXH:-1000}"
MINH="${MINH:-0}"
VERIFY_FACES="${VERIFY_FACES:-${default_verify_faces}}"
# 全量质量审计只占用自然计时的 repeat_0，不混入正式测量。
default_final_validation=0
[[ "${EXPERIMENT_PRESET}" != cooperate ]] || default_final_validation=1
QUALITY_WARMUP="${QUALITY_WARMUP:-${default_final_validation}}"
COMMUNICATION_ABLATION="${COMMUNICATION_ABLATION:-${default_final_validation}}"
export QUALITY_WARMUP COMMUNICATION_ABLATION
[[ "${QUALITY_WARMUP}" =~ ^[01]$ && "${COMMUNICATION_ABLATION}" =~ ^[01]$ ]] || exit 2
if [[ "${QUALITY_WARMUP}" == 1 ]]; then
    [[ "${WARMUPS}" =~ ^[1-9][0-9]*$ && " ${TIMING_MODES//,/ } " == *" natural "* &&
       ( "${EXPERIMENT_STAGE}" == kernel || "${EXPERIMENT_STAGE}" == communication ) ]] || {
        echo "QUALITY_WARMUP=1 要求至少一次预热、natural 计时及 kernel/communication 阶段。" >&2;exit 2;
    }
fi
CLEANUP_RESULTS="${CLEANUP_RESULTS:-1}"
RESUME="${RESUME:-1}"
BALANCE_SWEEPS="${BALANCE_SWEEPS:-4}"
CUT_GROWTH="${CUT_GROWTH:-0.05}"
COST_WEIGHTS="${COST_WEIGHTS:-1,1,1,1}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-7200}"
START_DELAY_SECONDS="${START_DELAY_SECONDS:-120}"
PARTITION="${PARTITION:-mt_module}"
SBATCH_COMMAND="${SBATCH_COMMAND:-yhbatch}"
SBATCH_EXTRA_ARGS="${SBATCH_EXTRA_ARGS:-}"
default_launcher=yhrun
[[ "${EXPERIMENT_PRESET}" != cooperate ]] || default_launcher="${SCRIPT_DIR}/node_affinity_yhrun.sh"
MPI_LAUNCHER="${MPI_LAUNCHER:-${default_launcher}}"
MPI_EXTRA_ARGS="${MPI_EXTRA_ARGS:---mpi=pmix}"
DRY_RUN="${DRY_RUN:-0}"
INPUT_PATH="${INPUT_PATH:-/vol8/home/hnu_lhz/cjz/NETGEN/test_code_expansion/inputData/wholewall3solid.STEP}"

export EXPERIMENT_PRESET PROCESS_COUNTS ALGORITHMS TIMING_MODES RANKS_PER_NODE
export REPEATS WARMUPS LEVELS REFINES MAXH MINH VERIFY_FACES CLEANUP_RESULTS RESUME
export BALANCE_SWEEPS CUT_GROWTH COST_WEIGHTS TIMEOUT_SECONDS START_DELAY_SECONDS
export PARTITION SBATCH_COMMAND SBATCH_EXTRA_ARGS MPI_LAUNCHER MPI_EXTRA_ARGS DRY_RUN INPUT_PATH
export EXPERIMENT_STAGE CALIBRATION_ROOT MIN_GAIN_SECONDS MIN_GAIN_FRACTION CLOSURE_GROWTH
export PARTITION_SEEDS SEARCH_SECONDS
[[ "${CPUS_PER_TASK}" =~ ^[1-9][0-9]*$ && "${KERNEL_THREADS}" =~ ^[0-9]+$ ]] || exit 2
(( KERNEL_THREADS<=CPUS_PER_TASK )) || { echo "内核线程数超过每进程预留核数。" >&2;exit 2; }
if [[ "${EXPERIMENT_STAGE}" == kernel ]]; then
    for threads in ${KERNEL_THREAD_COUNTS}; do
        [[ "$threads" =~ ^[1-9][0-9]*$ ]] && ((threads<=CPUS_PER_TASK)) || { echo "KERNEL_THREAD_COUNTS 必须为不超过 CPUS_PER_TASK 的正整数。" >&2;exit 2; }
    done
    for scheduler in ${KERNEL_SCHEDULERS}; do
        [[ "$scheduler" == static || "$scheduler" == cavity || "$scheduler" == repair || "$scheduler" == frontier || "$scheduler" == node_original || "$scheduler" == node_native || "$scheduler" == node_fixed || "$scheduler" == node_elastic ]] || { echo "未知内核策略：${scheduler}" >&2;exit 2; }
    done
    if [[ " ${KERNEL_SCHEDULERS} " == *" node_"* ]]; then
        [[ "${KERNEL_THREAD_COUNTS}" == "${CPUS_PER_TASK}" ]] && ((CPUS_PER_TASK>=2 && RANKS_PER_NODE>=2)) || {
            echo "节点协作要求单一初始线程数等于每进程预留核数，且每节点至少2进程、每进程至少2核。" >&2;exit 2;
        }
        for count in ${PROCESS_COUNTS}; do
            ((count>=RANKS_PER_NODE && count%RANKS_PER_NODE==0)) || { echo "协作实验进程数须填满节点。" >&2;exit 2; }
        done
    fi
    [[ -n "${KERNEL_THREAD_COUNTS}" && -n "${KERNEL_SCHEDULERS}" ]] || exit 2
fi
if [[ "${EXPERIMENT_STAGE}" == communication || "${EXPERIMENT_STAGE}" == kernel ]]; then
    [[ "${BALANCE_METHOD}" == none ]] || { echo "通信基线要求 BALANCE_METHOD=none。" >&2;exit 2; }
    for algorithm in ${ALGORITHMS//,/ }; do
        [[ "$algorithm" == baseline || "$algorithm" == sparse ]] || {
            echo "通信基线仅允许 baseline sparse；历史均衡需显式选择 evaluation 和对应方法。" >&2;exit 2;
        }
    done
    PLACEMENT_ROTATION=0
elif [[ "${BALANCE_METHOD}" == none ]]; then
    echo "历史实验需在统一配置区显式指定 BALANCE_METHOD。" >&2;exit 2
fi
if [[ "${BALANCE_METHOD}" == task_queue ]]; then
    [[ "${EXPERIMENT_STAGE}" == evaluation && "${TASK_COUNT}" =~ ^[1-9][0-9]*$ ]] || {
        echo "任务调度使用 evaluation，TASK_COUNT 为正整数。" >&2;exit 2;
    }
    PLACEMENT_ROTATION=0
fi
export PARTITION_VARIANT PLACEMENT_ROTATION BALANCE_METHOD TASK_COUNT TASK_CUT_GROWTH

# 登录节点：转入提交驱动；计算作业：继续执行下方 worker（工作进程）逻辑。
if [[ "${MESH_EXPERIMENT_WORKER:-0}" != 1 ]]; then
    export MESH_EXPERIMENT_DRIVER_READY=1
    exec "${SCRIPT_DIR}/submit_experiments.sh" "$@"
fi

if [[ "${EXPERIMENT_STAGE}" == kernel ]]; then
    kernel_root="${RUN_ROOT:?}";kernel_failures=0
    mkdir -p "${kernel_root}/p${PROCESS_COUNT:?}"
    read -r -a schedulers <<< "${KERNEL_SCHEDULERS}"
    # Interleave strategy runs by repetition; rotate which strategy runs first.
    # Each child retains the full plan/signature, but runs only this repetition.
    for ((kr=1-WARMUPS;kr<=REPEATS;++kr)); do
      for threads in ${KERNEL_THREAD_COUNTS}; do
        for ((ks=0;ks<${#schedulers[@]};++ks)); do
          scheduler="${schedulers[$(((ks+kr+WARMUPS-1)%${#schedulers[@]}))]}"
          variant="${kernel_root}/kernel_${scheduler}_t${threads}"
          variant_algorithms="${ALGORITHMS}"
          if [[ "${COMMUNICATION_ABLATION}" == 1 && "$scheduler" == node_original &&
                " ${ALGORITHMS//,/ } " != *" baseline "* ]]; then
              variant_algorithms="baseline ${ALGORITHMS}"
          fi
          if ! EXPERIMENT_STAGE=communication KERNEL_REPEAT="$kr" KERNEL_THREADS="$threads" KERNEL_SCHEDULER="$scheduler" \
               ALGORITHMS="$variant_algorithms" RUN_ROOT="$variant" bash "${SCRIPT_DIR}/run_experiments.sh"; then
              kernel_failures=$((kernel_failures+1))
          fi
        done
      done
    done
    python3 "${SCRIPT_DIR}/analyze_results.py" "$kernel_root" --kernel-overview --kernel-ranks "$PROCESS_COUNT"
    ((kernel_failures==0)) || exit 1
    exit 0
fi

[[ -r "${SCRIPT_DIR}/cluster_env.sh" && -r "${SCRIPT_DIR}/analyze_results.py" ]] || {
    echo "Cannot locate strong_scaling source directory: ${SCRIPT_DIR}" >&2
    exit 2
}
source "${SCRIPT_DIR}/cluster_env.sh"
PROCESS_COUNT="${PROCESS_COUNT:?worker requires PROCESS_COUNT}"
RUN_ROOT="${RUN_ROOT:?worker requires RUN_ROOT}"
BINARY="${BINARY:-${REPOSITORY_ROOT}/build/mesh_occ_mpi/mesh_occ_mpi}"
cleanup_args=()
case "${CLEANUP_RESULTS}" in
    0) cleanup_args+=(--keep-artifacts) ;;
    1) ;;
    *) echo "CLEANUP_RESULTS must be 0 or 1" >&2; exit 2 ;;
esac
command -v flock >/dev/null || { echo "Missing flock (run directory locking)" >&2; exit 2; }
read -r -a algorithms <<< "${ALGORITHMS//,/ }"
read -r -a timings <<< "${TIMING_MODES//,/ }"
read -r -a seeds <<< "${PARTITION_SEEDS//,/ }"
declare -A seen_seeds=()
for seed in "${seeds[@]}"; do
    [[ "${seed}" =~ ^(-1|0|[1-9][0-9]*)$ && ${#seed} -le 10 ]] && (( seed<=2147483647 )) || {
        echo "Invalid partition seed: ${seed}" >&2; exit 2;
    }
    [[ -z "${seen_seeds[${seed}]:-}" ]] || { echo "Duplicate partition seed" >&2; exit 2; }
    seen_seeds[${seed}]=1
done
(( ${#seeds[@]} > 0 )) || { echo "Missing partition seeds" >&2; exit 2; }
[[ "${PARTITION_VARIANT}" == cell_order_v1 || "${PARTITION_VARIANT}" == metis_seed ]] || exit 2
[[ "${PLACEMENT_ROTATION}" == 0 || "${PLACEMENT_ROTATION}" == 1 ]] || exit 2
if [[ "${EXPERIMENT_STAGE}" == calibration ]]; then
    (( ${#seeds[@]} >= 3 && REPEATS >= 3 )) && [[ "${PLACEMENT_ROTATION}" == 1 ]] || {
        echo "第三次校准需要至少3组分区、3次正式重复及节点轮换；修改统一配置区。" >&2; exit 2;
    }
fi
resource_evaluation=0
if [[ "${EXPERIMENT_STAGE}" == evaluation && "${BALANCE_METHOD}" == node_mapping ]]; then
    resource_evaluation=1
    [[ "${PARTITION_SEEDS}" == 41 && "${PARTITION_VARIANT}" == cell_order_v1 ]] || {
        echo "节点映射本轮固定使用留出分区 41；-1/17 已用于训练。" >&2;exit 2;
    }
fi
[[ "${BALANCE_METHOD}" == none || "${BALANCE_METHOD}" == node_mapping || "${BALANCE_METHOD}" == boundary || "${BALANCE_METHOD}" == task_queue ]] || exit 2
read -r -a mpi_extra <<< "${MPI_EXTRA_ARGS}"
for a in "${algorithms[@]}"; do
    case "$a" in baseline|balance|sparse|combined) ;; *) echo "Unknown algorithm: $a" >&2; exit 2;; esac
done
for mode in "${timings[@]}"; do
    case "$mode" in natural|split) ;; *) echo "Unknown timing mode: $mode" >&2; exit 2;; esac
done
[[ "${PROCESS_COUNT}" =~ ^[1-9][0-9]*$ && "${REPEATS}" =~ ^[1-9][0-9]*$ &&
   "${WARMUPS}" =~ ^[0-9]+$ && "${RANKS_PER_NODE}" =~ ^[1-9][0-9]*$ &&
   "${TIMEOUT_SECONDS}" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid run counts" >&2; exit 2; }
[[ -x "${BINARY}" && -f "${INPUT_PATH}" ]] || { echo "Missing executable or input: ${BINARY}, ${INPUT_PATH}" >&2; exit 2; }
(( ${#algorithms[@]} > 0 && ${#timings[@]} > 0 )) || exit 2

# A stale executable silently ignores the new research arguments in older
# revisions.  That produces ordinary mesh artifacts but no rank profile, and
# the failure would otherwise be discovered only after many expensive runs.
binary_error=""
if ((KERNEL_THREADS>0)); then
    [[ -r "${NETGEN_INSTALL_LIB}/libnglib.so" ]] || { echo "缺少本次内核库，请先运行 build_project.sh。" >&2;exit 2; }
    MESH_KERNEL_SHA256="$(sha256sum "${NETGEN_INSTALL_LIB}/libnglib.so" | cut -d ' ' -f1)"
    export MESH_KERNEL_SHA256
fi
markers=("--profile-core-only" "--algorithm" "research_1" "global_id_bits" "mesh_phase_v3")
if [[ "${EXPERIMENT_STAGE}" == communication ]]; then
    markers+=("mesh_comm_v1" "--communication-only")
else
    [[ "${EXPERIMENT_STAGE}" == legacy ]] || markers+=("partition_sampling_v1" "--preflight-parts")
fi
[[ "${BALANCE_METHOD}" != task_queue ]] || markers+=("mesh_tasks_v1" "--mesh-tasks")
((KERNEL_THREADS==0)) || markers+=("--kernel-threads" "--kernel-scheduler" "repair_v2")
[[ "${KERNEL_SCHEDULER}" != node_* ]] || markers+=("node_coop_v2")
[[ "${KERNEL_SCHEDULER}" != node_tail ]] || markers+=("node_tail_v1")
[[ "${KERNEL_SCHEDULER}" != node_budget && "${KERNEL_SCHEDULER}" != node_priority ]] || markers+=("node_work_v1")
[[ "${KERNEL_SCHEDULER}" != node_reserved && "${KERNEL_SCHEDULER}" != node_elastic ]] || markers+=("node_atomic_v1")
[[ "${KERNEL_SCHEDULER}" != node_* ]] || markers+=("node_timeline_v1" "node_stage_metrics_v1")
[[ "${KERNEL_SCHEDULER}" != node_stage && "${KERNEL_SCHEDULER}" != node_reclaim && "${KERNEL_SCHEDULER}" != node_selective && "${KERNEL_SCHEDULER}" != node_once ]] || markers+=("node_stage_v1")
[[ "${QUALITY_WARMUP}" != 1 ]] || markers+=("volume_audit_v1")
[[ "${KERNEL_SCHEDULER}" != node_original ]] || markers+=("node_original")
[[ "${KERNEL_SCHEDULER}" != node_selective ]] || markers+=("finalopt_only_v1")
[[ "${KERNEL_SCHEDULER}" != node_once ]] || markers+=("finalopt_once_v1")
((resource_evaluation==0)) || markers+=("mesh_resource_v1" "--rank-capacities")
for marker in "${markers[@]}"; do
    if ! LC_ALL=C grep -aFq -- "${marker}" "${BINARY}"; then
        binary_error="missing capability marker ${marker}"
        break
    fi
done
if [[ -z "${binary_error}" ]]; then
    newer_source="$(find "${REPOSITORY_ROOT}/mesh_occ_mpi" -type f \
        \( -name '*.cpp' -o -name '*.h' -o -name 'CMakeLists.txt' \) \
        -newer "${BINARY}" -print -quit)"
    [[ -z "${newer_source}" ]] || binary_error="source is newer than executable: ${newer_source}"
fi
if [[ -n "${binary_error}" ]]; then
    echo "ERROR: stale/incompatible mesh executable (${binary_error})." >&2
    echo "Rebuild first: cmake --build \"${REPOSITORY_ROOT}/build\" -j" >&2
    echo "No experiment was started and no result directory needs analysis." >&2
    exit 2
fi
export MESH_INPUT_SHA256="$(sha256sum "${INPUT_PATH}" | cut -d ' ' -f1)"
export MESH_BINARY_SHA256="$(sha256sum "${BINARY}" | cut -d ' ' -f1)"
MESH_SOURCE_REVISION="${MESH_SOURCE_REVISION:-$(git -C "${REPOSITORY_ROOT}" rev-parse HEAD)}"
[[ -n "${MESH_SOURCE_REVISION}" ]] || { echo "Cannot record source revision" >&2; exit 2; }
export MESH_SOURCE_REVISION
export MESH_MODEL_SHA256="none"
model_args=()
export MESH_CAPACITY_SHA256="none"
resource_check=()
if ((resource_evaluation)); then
    model="${RUN_ROOT}/models/p${PROCESS_COUNT}.mapping"
    resource_check=(--model "${model}" --target-ranks "${PROCESS_COUNT}" --levels "${LEVELS}"
                    --refines "${REFINES}" --rpn "${RANKS_PER_NODE}")
    python3 "${SCRIPT_DIR}/resource_model.py" "${resource_check[@]}"
    export MESH_MODEL_SHA256="$(sha256sum "${model}" | cut -d ' ' -f1)"
    model_args=(--resource-model "${model}" --min-gain-seconds "${MIN_GAIN_SECONDS}"
                --min-gain-fraction "${MIN_GAIN_FRACTION}")
elif [[ "${EXPERIMENT_STAGE}" == evaluation && "${BALANCE_METHOD}" != task_queue ]]; then
    model="${RUN_ROOT}/models/p${PROCESS_COUNT}.model"
    python3 "${SCRIPT_DIR}/fit_cost_model.py" --verify "${model}" --levels "${LEVELS}" --refines "${REFINES}" \
        --target-ranks "${PROCESS_COUNT}" --input-sha "${MESH_INPUT_SHA256}" --binary-sha "${MESH_BINARY_SHA256}" \
        --maxh "${MAXH}" --minh "${MINH}" --threads "${OMP_NUM_THREADS}" --require-sampling --variant "${PARTITION_VARIANT}"
    export MESH_MODEL_SHA256="$(sha256sum "${model}" | cut -d ' ' -f1)"
    model_args=(--phase-model "${model}" --min-gain-seconds "${MIN_GAIN_SECONDS}"
                --min-gain-fraction "${MIN_GAIN_FRACTION}" --closure-growth "${CLOSURE_GROWTH}"
                --search-seconds "${SEARCH_SECONDS}")
fi
# Every submitted job has the SAME earliest launch time; no serial dependencies.
while (( $(date +%s) < ${START_EPOCH:-0} )); do
    remaining=$(( START_EPOCH - $(date +%s) ))
    (( remaining > 30 )) && remaining=30
    (( remaining > 0 )) && sleep "${remaining}"
done
pdir="${RUN_ROOT}/p${PROCESS_COUNT}"
mkdir -p "${pdir}"
status_file="${pdir}/run_status.tsv"
[[ -f "${status_file}" ]] || printf 'algorithm\ttiming\trepeat\texit_code\tdirectory\tpartition_seed\n' > "${status_file}"
failure_file="${pdir}/failures.log"
launch=("${MPI_LAUNCHER}" "${mpi_extra[@]}" -n "${PROCESS_COUNT}")
case "$(basename "${MPI_LAUNCHER}")" in yhrun|srun)
    launch+=(-N "$(( (PROCESS_COUNT+RANKS_PER_NODE-1)/RANKS_PER_NODE ))")
    if ((KERNEL_THREADS>0)); then
        binding=cores
        [[ "${EXPERIMENT_PRESET}" != cooperate && "${KERNEL_SCHEDULER}" != node_* ]] || binding=threads
        launch+=(--cpus-per-task "${CPUS_PER_TASK}" --cpu-bind "${binding}")
    fi
    [[ "${EXPERIMENT_STAGE}" == legacy ]] || launch+=(--ntasks-per-node "${RANKS_PER_NODE}" --distribution block)
    ;;
esac
common=(-i "${INPUT_PATH}" -l "${LEVELS}" -r "${REFINES}" --maxh "${MAXH}" --minh "${MINH}" -adj
        --partition-variant "${PARTITION_VARIANT}")
if [[ "${EXPERIMENT_STAGE}" == communication ]]; then
    common+=(--communication-only)
else
    common+=(--balance-sweeps "${BALANCE_SWEEPS}" --cut-growth "${CUT_GROWTH}" --cost-weights "${COST_WEIGHTS}"
             "${model_args[@]}")
fi
if ((KERNEL_THREADS>0)); then
    common+=(--kernel-threads "${KERNEL_THREADS}" --kernel-scheduler "${KERNEL_SCHEDULER}")
fi
if [[ "${BALANCE_METHOD}" == task_queue ]]; then
    (( PROCESS_COUNT>=2 && TASK_COUNT>=PROCESS_COUNT-1 )) || { echo "TASK_COUNT 必须不少于进程数减一。" >&2;exit 2; }
    common+=(--mesh-tasks "${TASK_COUNT}" --task-cut-growth "${TASK_CUT_GROWTH}")
fi
config_text="$(printf '%s\n' "${PROCESS_COUNT}" "${ALGORITHMS}" "${TIMING_MODES}" "${REPEATS}" "${WARMUPS}" "${common[@]}" "PARTITION_SEEDS=${PARTITION_SEEDS}" "PLACEMENT_ROTATION=${PLACEMENT_ROTATION}" "RANKS_PER_NODE=${RANKS_PER_NODE}" "SOURCE_REVISION=${MESH_SOURCE_REVISION}" "OMP_NUM_THREADS=${OMP_NUM_THREADS}" "CPUS_PER_TASK=${CPUS_PER_TASK}" "MESH_KERNEL_SHA256=${MESH_KERNEL_SHA256:-none}" "KERNEL_ORDER=interleaved_v2" "QUALITY_WARMUP=${QUALITY_WARMUP}" "COMMUNICATION_ABLATION=${COMMUNICATION_ABLATION}" "EXPERIMENT_STAGE=${EXPERIMENT_STAGE}" "MODEL_SHA256=${MESH_MODEL_SHA256}"; sha256sum "${BINARY}" "${INPUT_PATH}")"
if [[ -f "${pdir}/configuration.txt" && "$(cat "${pdir}/configuration.txt")" != "${config_text}" ]]; then
    echo "Existing results use another configuration; choose a new RUN_ROOT." >&2
    exit 2
fi
printf '%s\n' "${config_text}" > "${pdir}/configuration.txt"

# 每个并行作业先用一个进程检查其正式分区规模，不生成细网格。
# 只保留三份粗单元归属与一个摘要；失败立即停止本规模的昂贵采样。
preflight="${pdir}/partition_preflight"
preflight_seeds=("${seeds[@]}")
((resource_evaluation==0)) || preflight_seeds+=(-1)
preflight_failed() {
    printf '分区预检未通过，未启动细网格采样。请查看 partition_preflight/run.log。\n' > "${pdir}/RESULT_SUMMARY.txt"
    cat "${pdir}/RESULT_SUMMARY.txt" >&2
    exit 2
}
if [[ "${EXPERIMENT_STAGE}" != legacy && "${EXPERIMENT_STAGE}" != communication && "${BALANCE_METHOD}" != task_queue ]]; then
    mkdir -p "${preflight}"
    preflight_check=(--levels "${LEVELS}" --refines "${REFINES}" --target-ranks "${PROCESS_COUNT}"
        --input-sha "${MESH_INPUT_SHA256}" --binary-sha "${MESH_BINARY_SHA256}"
        --maxh "${MAXH}" --minh "${MINH}" --threads "${OMP_NUM_THREADS}"
        --seeds "${preflight_seeds[@]}" --variant "${PARTITION_VARIANT}")
    if [[ -f "${preflight}/manifest.json" ]]; then
        python3 "${SCRIPT_DIR}/fit_cost_model.py" --verify-preflight "${preflight}" "${preflight_check[@]}" \
            >> "${preflight}/run.log" 2>&1 || preflight_failed
    else
        probe=("${MPI_LAUNCHER}" "${mpi_extra[@]}" -n 1)
        case "$(basename "${MPI_LAUNCHER}")" in yhrun|srun) probe+=(-N 1 --ntasks-per-node 1);; esac
        if ! timeout "${TIMEOUT_SECONDS}" "${probe[@]}" "${BINARY}" \
            -i "${INPUT_PATH}" -l "${LEVELS}" -r "${REFINES}" --maxh "${MAXH}" --minh "${MINH}" \
            --algorithm sparse --partition-variant "${PARTITION_VARIANT}" --profile-core-only \
            --preflight-parts "${PROCESS_COUNT}" --preflight-seeds "${preflight_seeds[*]}" \
            --preflight-dir "${preflight}" -o "${preflight}/mesh/" > "${preflight}/run.log" 2>&1; then
            preflight_failed
        fi
        python3 "${SCRIPT_DIR}/fit_cost_model.py" --seal-preflight "${preflight}" \
            "${preflight_check[@]}" --binary "${BINARY}" >> "${preflight}/run.log" 2>&1 || preflight_failed
    fi
    export MESH_PREFLIGHT_SHA256="$(sha256sum "${preflight}/manifest.json" | cut -d ' ' -f1)"
fi
# 一次当前节点参考预热，冻结能力后运行全部四组；不新增校准作业。
if ((resource_evaluation)); then
    capacity="${pdir}/node_capacities.txt"
    python3 "${SCRIPT_DIR}/resource_model.py" "${resource_check[@]}" --preflight "${preflight}"
    if [[ ! -f "${capacity}" ]]; then
        if [[ -n "$(find "${pdir}" -path '*/repeat_*/SUCCESS' -print -quit)" ]]; then
            echo "已有正式结果但节点能力文件缺失，不能重估后混用；请使用新结果目录。" >&2;exit 2
        fi
        warm="${pdir}/capacity_warmup"
        mkdir -p "${warm}"
        export MESH_PARTITION_SIGNATURE="$(python3 - "${preflight}/manifest.json" <<'REFERENCE'
import json,sys
print(json.load(open(sys.argv[1]))['partitions']['-1']['signature'])
REFERENCE
        )"
        started=$(date +%s)
        if ! timeout "${TIMEOUT_SECONDS}" "${launch[@]}" "${BINARY}" "${common[@]}" \
            --algorithm sparse --partition-seed -1 --rank-shift 0 \
            --partition-reference "${preflight}/seed_-1.labels" --profile-core-only --profile-natural \
            --profile-dir "${warm}" --profile-experiment capacity_warmup --profile-repeat 0 \
            -o "${warm}/mesh/" > "${warm}/run.log" 2>&1; then
            echo "节点能力预热失败；查看 capacity_warmup/run.log。" > "${pdir}/RESULT_SUMMARY.txt"
            exit 2
        fi
        python3 "${SCRIPT_DIR}/analyze_results.py" "${warm}" --finish-run "${cleanup_args[@]}"
        profile="${warm}/rank_profiles.jsonl.gz"
        [[ -f "${profile}" ]] || profile="${warm}/rank_profiles.jsonl"
        python3 "${SCRIPT_DIR}/resource_model.py" "${resource_check[@]}" --preflight "${preflight}" \
            --profile "${profile}" --output "${capacity}" --elapsed "$(( $(date +%s)-started ))"
    fi
    python3 "${SCRIPT_DIR}/resource_model.py" "${resource_check[@]}" --preflight "${preflight}" \
        --verify-capacity "${capacity}"
    export MESH_CAPACITY_SHA256="$(sha256sum "${capacity}" | cut -d ' ' -f1)"
    common+=(--rank-capacities "${capacity}")
fi
# 可选独立面参考检查；默认全量审计已将参考检查合并到自然预热轮。
if [[ "${VERIFY_FACES}" == 1 ]]; then
  for seed in "${seeds[@]}"; do
    verify_algorithms="sparse combined"
    [[ "${EXPERIMENT_STAGE}" != communication ]] || verify_algorithms="sparse"
    for a in ${verify_algorithms}; do
        checkdir="${pdir}/verify_${a}_seed${seed}"
        mkdir -p "${checkdir}"
        timeout "${TIMEOUT_SECONDS}" "${launch[@]}" "${BINARY}" "${common[@]}" \
            --algorithm "${a}" --partition-seed "${seed}" --verify-faces -v -o "${checkdir}/" > "${checkdir}/run.log" 2>&1 || exit $?
    done
  done
fi
python3 - "${pdir}/plan.json" "${PROCESS_COUNT}" "${REPEATS}" "${ALGORITHMS}" "${TIMING_MODES}" "${PARTITION_SEEDS}" "${QUALITY_WARMUP}" <<'PLAN'
import json,pathlib,sys
path,n,repeats,algorithms,timings,seeds,quality=sys.argv[1:]
pathlib.Path(path).write_text(json.dumps(dict(ranks=int(n), repeats=int(repeats),
    algorithms=algorithms.replace(',', ' ').split(), timings=timings.replace(',', ' ').split(),
    partition_seeds=list(map(int,seeds.replace(',', ' ').split())), quality_warmup=quality=="1")))
PLAN
failures=0
# Negative/zero repeats are warmups. Rotate mode order to avoid always giving
# one algorithm the first file-cache/allocator state. No cache eviction code.
for ((rep=1-WARMUPS;rep<=REPEATS;++rep)); do
  [[ -z "${KERNEL_REPEAT:-}" || "${KERNEL_REPEAT}" == "$rep" ]] || continue
  rank_shift=0
  if [[ "${PLACEMENT_ROTATION}" == 1 && "${EXPERIMENT_STAGE}" != legacy ]] && (( rep>0 )); then
    stride=$(( (PROCESS_COUNT/RANKS_PER_NODE/3)*RANKS_PER_NODE ))
    (( stride>=RANKS_PER_NODE )) || stride="${RANKS_PER_NODE}"
    rank_shift=$(( ((rep-1)%3)*stride%PROCESS_COUNT ))
  fi
  for ((s=0;s<${#seeds[@]};++s)); do
    seed="${seeds[$(( (s+rep+WARMUPS-1)%${#seeds[@]} ))]}"
    reference_args=()
    if [[ "${EXPERIMENT_STAGE}" != legacy && "${EXPERIMENT_STAGE}" != communication && "${BALANCE_METHOD}" != task_queue ]]; then
        reference_args=(--partition-reference "${preflight}/seed_${seed}.labels")
        export MESH_PARTITION_SIGNATURE="$(python3 - "${preflight}/manifest.json" "${seed}" <<'SIGNATURE'
import json,sys
print(json.load(open(sys.argv[1]))['partitions'][sys.argv[2]]['signature'])
SIGNATURE
        )"
    fi
    for mode in "${timings[@]}"; do
        for ((j=0;j<${#algorithms[@]};++j)); do
            a="${algorithms[$(( (j+rep+WARMUPS-1)%${#algorithms[@]} ))]}"
            out="${pdir}/${a}_${mode}/repeat_${rep}"
            [[ "${seed}" == -1 ]] || out="${pdir}/${a}_seed${seed}_${mode}/repeat_${rep}"
            mkdir -p "${out}"
            exec 9>>"${out}/.run.lock"
            if ! flock -n 9; then
                echo "Run directory is busy: ${out}" >&2
                failures=$((failures+1));exec 9>&-;continue
            fi
            if [[ "${RESUME}" == 1 && -f "${out}/SUCCESS" &&
                  ( -f "${out}/rank_profiles.jsonl" || -f "${out}/rank_profiles.jsonl.gz" ) ]]; then
                exec 9>&-;continue
            fi
            rm -f "${out}/SUCCESS" "${out}/rank_profiles.jsonl" "${out}/rank_profiles.jsonl.gz" "${out}/run.log.gz" "${out}/quality_summary.json"
            touch "${out}/RUNNING"
            args=("${common[@]}" --algorithm "${a}" --partition-seed "${seed}" --rank-shift "${rank_shift}"
                  "${reference_args[@]}" --profile-core-only --profile-dir "${out}"
                  --profile-experiment "${a}" --profile-repeat "${rep}" -o "${out}/mesh/")
            [[ "${mode}" == natural ]] && args+=(--profile-natural)
            if [[ "${QUALITY_WARMUP}" == 1 && "$rep" == 0 && "$mode" == natural ]]; then
                args+=(--validate-volume)
                [[ "$a" != sparse ]] || args+=(--verify-faces)
            fi
            rc=0
            timeout --kill-after=30s "${TIMEOUT_SECONDS}" "${launch[@]}" "${BINARY}" "${args[@]}" > "${out}/run.log" 2>&1 || rc=$?
            rm -f "${out}/RUNNING"
            exec 9>&-
            if ((rc==0)); then
                finalize_error=""
                if ! finalize_error="$(python3 "${SCRIPT_DIR}/analyze_results.py" "${out}" --finish-run "${cleanup_args[@]}" 2>&1)"; then
                    rc=1
                    printf '%s/%s seed %s repeat %s: %s\n' "${a}" "${mode}" "${seed}" "${rep}" "${finalize_error}" >> "${failure_file}"
                fi
            fi
            if ((rc!=0)); then
                failures=$((failures+1))
            fi
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "${a}" "${mode}" "${rep}" "${rc}" "${out}" "${seed}" >> "${status_file}"
        done
    done
  done
done
if [[ -n "${KERNEL_REPEAT:-}" && "${KERNEL_REPEAT}" != "${REPEATS}" ]]; then
    ((failures==0));exit $?
fi
[[ "${EXPERIMENT_STAGE}" == calibration ]] && cleanup_args+=(--require-calibration)
python3 "${SCRIPT_DIR}/analyze_results.py" "${pdir}" "${cleanup_args[@]}" || exit $?
((failures==0)) || exit 1

