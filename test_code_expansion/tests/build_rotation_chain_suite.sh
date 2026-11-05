#!/usr/bin/env bash
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Retain the user's working build environment. Do not purge loaded modules.
if [[ -z "${ROTATION_CMAKE:-}" ]]; then
    resolved_cmake="$(type -P cmake || true)"
    [[ -z "${resolved_cmake}" ]] || export ROTATION_CMAKE="${resolved_cmake}"
fi
envfile="${ROTATION_CLUSTER_ENV:-${root}/test_code_expansion/strong_scaling/cluster_env.sh}"
LOAD_MODULES=0 CLUSTER_ENV_STRICT=0 source "${envfile}"
export CC="${ROTATION_CC:-${GCCHOME}/bin/gcc}"
export CXX="${ROTATION_CXX:-${GCCHOME}/bin/g++}"
exec python3 "${root}/test_code_expansion/tests/build_rotation_chain_suite.py" "$@"
