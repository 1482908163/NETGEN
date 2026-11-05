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
    mkdir -p "${ROTATION_SUITE_ROOT}/build/rotation_chain_logs"
    exec yhbatch -p "${ROTATION_PARTITION:-mt_module}" -N 1 -n 1 -c 4 \
        --export=ALL --job-name=rotation-suite \
        --output="${ROTATION_SUITE_ROOT}/build/rotation_chain_logs/suite-%j.log" \
        "${ROTATION_SUITE_ROOT}/test_code_expansion/tests/run_rotation_chain_suite.sh" --job "$@"
fi
if [[ "${1:-}" == --job ]]; then
    shift
    [[ -n "${SLURM_JOB_ID:-}" ]] || { echo '--job 只能在已分配作业内使用' >&2; exit 2; }
    exec yhrun -N 1 -n 1 -c 4 --cpu-bind=cores python3 "${suite}" "$@"
fi
exec python3 "${suite}" "$@"
