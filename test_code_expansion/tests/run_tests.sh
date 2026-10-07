#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="${TEST_BUILD_DIR:-${ROOT}/build/research_tests}"
CXX="${CXX:-g++}"
MPICXX="${MPICXX:-mpicxx}"
MPI_LAUNCHER="${MPI_LAUNCHER:-mpiexec}"
TEST_PROCESS_COUNTS="${TEST_PROCESS_COUNTS:-1 2 3 4 8}"
mkdir -p "${BUILD}"
"${CXX}" -std=c++17 -Wall -Wextra -Werror -pthread "${ROOT}/tests/test_deterministic_refine.cpp" -o "${BUILD}/test_deterministic_refine"
"${BUILD}/test_deterministic_refine"
for name in box_index partition_cost face_dependency mesh_ids adjacency_audit worklet_schedule worklet_failure node_window; do
    "${CXX}" -std=c++17 -Wall -Wextra -Werror -I "${ROOT}/mesh_occ_mpi" \
        "${ROOT}/tests/test_${name}.cpp" -o "${BUILD}/test_${name}"
    "${BUILD}/test_${name}"
done
"${MPICXX}" -std=c++17 -Wall -Wextra -Werror -I "${ROOT}/mesh_occ_mpi" \
    "${ROOT}/tests/test_sparse_faces.cpp" "${ROOT}/mesh_occ_mpi/scaling_profiler.cpp" \
    -o "${BUILD}/test_sparse_faces"
"${MPICXX}" -std=c++17 -Wall -Wextra -Werror -I "${ROOT}/mesh_occ_mpi" \
    "${ROOT}/tests/test_mesh_ids_mpi.cpp" -o "${BUILD}/test_mesh_ids_mpi"
"${MPICXX}" -std=c++17 -I "${ROOT}/mesh_occ_mpi" "${ROOT}/tests/test_node_resources.cpp" -pthread -o "${BUILD}/test_node_resources"
"${BUILD}/test_node_resources"
"${MPICXX}" -std=c++17 -Wall -Wextra -Werror -I "${ROOT}/mesh_occ_mpi" "${ROOT}/tests/test_ready_neighbors.cpp" -pthread -o "${BUILD}/test_ready_neighbors"
read -r -a extra <<< "${MPI_EXTRA_ARGS:-}"
for p in ${TEST_PROCESS_COUNTS}; do
    timeout "${TEST_TIMEOUT_SECONDS:-60}" "${MPI_LAUNCHER}" "${extra[@]}" -n "${p}" "${BUILD}/test_ready_neighbors"
    timeout "${TEST_TIMEOUT_SECONDS:-60}" "${MPI_LAUNCHER}" "${extra[@]}" -n "${p}" "${BUILD}/test_mesh_ids_mpi"
    for mode in natural split; do
        args=("${BUILD}/test_sparse_faces" "${BUILD}/p${p}_${mode}")
        [[ "${mode}" == split ]] && args+=(split)
        timeout "${TEST_TIMEOUT_SECONDS:-60}" "${MPI_LAUNCHER}" "${extra[@]}" -n "${p}" "${args[@]}"
    done
done
python3 "${ROOT}/tests/test_ready_protocol.py"
python3 "${ROOT}/tests/test_runner.py"
python3 "${ROOT}/tests/test_worklet_routes.py"
python3 "${ROOT}/tests/test_joint_runner.py"
python3 "${ROOT}/tests/test_abc_runner.py"
python3 "${ROOT}/tests/test_structural_runner.py"
python3 "${ROOT}/tests/test_recovery_runner.py"
python3 "${ROOT}/tests/test_tail_profile_runner.py"
python3 "${ROOT}/tests/test_refine_orientation.py"

python3 "${ROOT}/tests/test_refine_bulk_runner.py"

python3 "${ROOT}/tests/test_legal_split_rejection.py"
python3 "${ROOT}/tests/test_legal_prune_runner.py"

python3 "${ROOT}/tests/test_recovery_coloring.py"
python3 "${ROOT}/tests/test_recovery_batch_runner.py"

python3 "${ROOT}/tests/test_recovery_scale_config.py"
python3 "${ROOT}/tests/test_recovery_scale_runner.py"

python3 "${ROOT}/tests/test_smooth_balance_coloring.py"
python3 "${ROOT}/tests/test_smooth_balance_config.py"
python3 "${ROOT}/tests/test_smooth_balance_runner.py"
python3 "${ROOT}/tests/test_smooth_balance_validation.py"

python3 "${ROOT}/tests/test_split_reuse_transaction.py"
python3 "${ROOT}/tests/test_split_reuse_config.py"
python3 "${ROOT}/tests/test_split_reuse_validation.py"
python3 "${ROOT}/tests/test_split_reuse_runner.py"

"${CXX}" -std=c++17 -Wall -Wextra -Werror "${ROOT}/tests/test_front_face_index.cpp" -o "${BUILD}/test_front_face_index"
"${BUILD}/test_front_face_index"
python3 "${ROOT}/tests/test_front_topology_config.py"
python3 "${ROOT}/tests/test_front_topology_validation.py"
python3 "${ROOT}/tests/test_front_topology_runner.py"

python3 "${ROOT}/tests/test_front_bound_geometry.py"
python3 "${ROOT}/tests/test_front_bound_state.py"
python3 "${ROOT}/tests/test_front_bound_config.py"
python3 "${ROOT}/tests/test_front_bound_validation.py"
python3 "${ROOT}/tests/test_front_bound_runner.py"
