#!/usr/bin/env bash
set -Eeuo pipefail
# One entry for local execution or one optional cluster job. The exported root
# also works when Slurm executes a spool copy under /tmp/slurmd.
if [[ -z "${ROTATION_SUITE_ROOT:-}" ]]; then
    ROTATION_SUITE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
export ROTATION_SUITE_ROOT
suite="${ROTATION_SUITE_ROOT}/test_code_expansion/tests/run_rotation_chain_suite.py"
[[ -f "${suite}" ]] || { echo "找不到测试入口：${suite}" >&2; exit 2; }
if [[ "${1:-}" == --submit ]]; then
    shift
    command -v yhbatch >/dev/null || { echo '未找到 yhbatch；本地执行请去掉 --submit' >&2; exit 2; }
    # Read source/binary hashes only: never compile while submitting a job.
    python3 "${suite}" --check-build "$@"
    logdir="${ROTATION_SUITE_ROOT}/test_code_expansion/strong_scaling_results/rotation_chain_jobs"
    mkdir -p "${logdir}"
    exec yhbatch -p "${ROTATION_PARTITION:-mt_module}" -N 1 -n 1 -c 4 \
        --export=ALL --job-name=rotation-suite --chdir="${ROTATION_SUITE_ROOT}" \
        --output="${logdir}/job-%j.log" \
        "${ROTATION_SUITE_ROOT}/test_code_expansion/tests/run_rotation_chain_suite.sh" --job "$@"
fi
if [[ "${1:-}" == --job ]]; then
    shift
    [[ -n "${SLURM_JOB_ID:-}" ]] || { echo '--job 只能在已分配作业内使用' >&2; exit 2; }
    envfile="${ROTATION_CLUSTER_ENV:-${ROTATION_SUITE_ROOT}/test_code_expansion/strong_scaling/cluster_env.sh}"
    [[ -r "${envfile}" ]] || { echo "找不到集群环境脚本：${envfile}" >&2; exit 2; }
    LOAD_MODULES=0 CLUSTER_ENV_STRICT=0 source "${envfile}"
    echo "旋转链分项测试：1 节点、1 进程、4 核；仅运行已编译程序"
    exec yhrun -N 1 -n 1 -c 4 --cpu-bind=cores python3 "${suite}" "$@"
fi
exec python3 "${suite}" "$@"
