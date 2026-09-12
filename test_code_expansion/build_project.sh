#!/bin/bash
set -e

PROJ_DIR="${PROJ_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
BUILD_DIR=$PROJ_DIR/build
NETGEN_ROOT="$(cd "${PROJ_DIR}/.." && pwd)"
NETGEN_INSTALL_DIR="${NETGEN_INSTALL_DIR:-${NETGEN_ROOT}/install}"
BUILD_JOBS="${BUILD_JOBS:-8}"
GCCHOME=/vol8/home/hnu_lhz/cjz/gcc-12
LOCAL_LIB=/vol8/home/hnu_lhz/cjz/lib/usr/lib/aarch64-linux-gnu

echo ">>> 加载必要的模块..."
module purge
module load mpich/mpi-x

echo ">>> 设置 gcc-12 环境..."
export PATH=$GCCHOME/bin:$PATH
export LD_LIBRARY_PATH=$GCCHOME/lib64:$LD_LIBRARY_PATH

# 让 MPICH wrapper（MPI 编译器封装）使用 gcc-12
export MPICH_CC=$GCCHOME/bin/gcc
export MPICH_CXX=$GCCHOME/bin/g++

# 补充编译和运行需要的动态库路径
export LIBRARY_PATH=$LOCAL_LIB:/usr/lib/aarch64-linux-gnu:$LIBRARY_PATH
export LD_LIBRARY_PATH=$LOCAL_LIB:/usr/lib/aarch64-linux-gnu:${NETGEN_INSTALL_DIR}/lib:/vol8/home/hnu_lhz/cjz/install_libs/lib:$LD_LIBRARY_PATH

echo ">>> 检查 MPI 编译器..."
which mpicc
which mpicxx

echo ">>> mpicc -show"
mpicc -show

echo ">>> mpicxx -show"
mpicxx -show

echo ">>> 显示当前环境..."
module list
echo "LIBRARY_PATH: $LIBRARY_PATH"
echo "LD_LIBRARY_PATH: $LD_LIBRARY_PATH"

echo ">>> 构建同仓库 Netgen 内核（包含候选调度接口）..."
# 节点租约负责核绑定；禁止任务管理器内部 NUMA 自动绑核覆盖租约核集合。
cmake -S "${NETGEN_ROOT}/netgen" -B "${NETGEN_ROOT}/build-netgen" \
  -DCMAKE_INSTALL_PREFIX="${NETGEN_INSTALL_DIR}" \
  -DCMAKE_C_COMPILER="${GCCHOME}/bin/gcc" -DCMAKE_CXX_COMPILER="${GCCHOME}/bin/g++" \
  -DCMAKE_PREFIX_PATH="${NETGEN_INSTALL_DIR}" \
  -DUSE_GUI=OFF -DUSE_PYTHON=OFF -DUSE_OCC=ON -DUSE_MPI=ON -DUSE_NUMA=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build "${NETGEN_ROOT}/build-netgen" -j "${BUILD_JOBS}"
cmake --install "${NETGEN_ROOT}/build-netgen"

echo ">>> 清理旧 build 目录..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

echo ">>> 运行 CMake 配置..."
cmake \
  -DNETGEN_INSTALL_DIR="${NETGEN_INSTALL_DIR}" \
  -DCMAKE_INSTALL_PREFIX="${NETGEN_INSTALL_DIR}" \
  -DCMAKE_C_COMPILER="$(which mpicc)" \
  -DCMAKE_CXX_COMPILER="$(which mpicxx)" \
  .. || {
    echo "❌ CMake 配置失败"
    exit 1
  }

echo ">>> 开始编译..."
make -j"${BUILD_JOBS}" || {
  echo "❌ Make 编译失败"
  exit 1
}

echo ">>> 开始安装..."
make install || {
  echo "❌ Make install 失败"
  exit 1
}

echo "✅ 编译完成"
echo "安装路径: ${NETGEN_INSTALL_DIR}"
echo "可执行文件: $BUILD_DIR/mesh_occ_mpi/mesh_occ_mpi"
