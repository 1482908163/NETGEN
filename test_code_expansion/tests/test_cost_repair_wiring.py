#!/usr/bin/env python3
"""Execute the production repair control flow against a small meshing seam."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
source=(ROOT.parent/'netgen/libsrc/meshing/meshfunc.cpp').read_text()
start=source.index('  void RemoveIllegalElements (Mesh & mesh3d, const MeshingParameters & options, int domain)')
opening=source.index('{',start);depth=1;end=opening+1
while depth:
    depth+=(source[end]=='{')-(source[end]=='}');end+=1
body=source[start:end]
prefix=r'''
#include "volume_cost_profile.hpp"
#include <algorithm>
#include <cassert>
#include <string>
#include <vector>
namespace netgen {
using std::min;
struct VolumeKernelStats { void Add(int,int) {} };
struct VolumeResources { bool grouped_repair=false; };
struct MeshingParameters {
  VolumeResources *volume_resources=nullptr;
  VolumeKernelStats *volume_kernel_stats=nullptr,*volume_legal_split_stats=nullptr;
  VolumeCostStats *volume_cost_stats=nullptr;
  int volume_cost_phase=0,nthreads=1;
  bool volume_parallel_repair=false,parallel_meshing=false;
  bool volume_repair_frontier=false,volume_candidate_schedule=false,volume_legal_split_prune=false;
};
struct Mesh {
  int marks=0;
  std::vector<std::string> operations;
  int GetNE() const { return 7; }
  int GetNP() const { return 5; }
  void CalcSurfacesOfNode() {}
  int MarkIllegalElements(int) { ++marks;return marks<4; }
};
struct VolumeResourceTeam {
  VolumeResourceTeam(VolumeResources*,int,int,int) {}
  void Checkpoint(int) {}
};
struct VolumeResourceScope {
  int threads=0;
  VolumeResourceScope(const VolumeResources*,int,int,int) {}
};
struct RegionTaskManager { explicit RegionTaskManager(int) {} };
struct VolumeKernelTimer { VolumeKernelTimer(VolumeKernelStats*,int) {} };
struct Timer { explicit Timer(const char*) {} };
struct RegionTimer { explicit RegionTimer(Timer&) {} };
struct {bool terminate=false;} multithread;
template<class... A> void PrintMessage(A...) {}
enum {OPT_LEGAL=4};
struct MeshOptimize3d {
  Mesh &mesh;const MeshingParameters &mp;
  MeshOptimize3d(Mesh &m,const MeshingParameters &p,int goal):mesh(m),mp(p) {
    assert(goal==OPT_LEGAL);
    // Forwarding diagnostics must not replace the native default algorithm
    // parameters with the caller's nthreads/parallel_meshing settings.
    assert(mp.nthreads==1 && !mp.parallel_meshing);
  }
  void operation(int op,const char*name) {
    VolumeCostScope scope(mp.volume_cost_stats,mp.volume_cost_phase,op,OPT_LEGAL,mesh.GetNP(),mesh.GetNE());
    mesh.operations.push_back(name);
  }
  void SplitImprove() {operation(1,"split");}
  void SwapImprove() {operation(2,"swap");}
  void SwapImprove2() {operation(3,"swap2");}
};
'''
suffix=r'''
}
int main() {
  using namespace netgen;
  MeshingParameters mp;mp.nthreads=4;mp.parallel_meshing=true;mp.volume_parallel_repair=true;
  Mesh reference;RemoveIllegalElements(reference,mp,0);
  for(int phase:{0,1}) {
    VolumeCostStats stats;mp.volume_cost_stats=&stats;mp.volume_cost_phase=phase;
    Mesh tested;RemoveIllegalElements(tested,mp,0);
    assert(tested.operations==reference.operations && tested.marks==reference.marks);
    assert((tested.operations==std::vector<std::string>{"split","swap","swap2"}));
    for(int p=0;p<3;++p)for(int op=0;op<4;++op) {
      const int base=(p*4+op)*VolumeCostStats::width;
      assert(stats.values[base+VolumeCostStats::calls].load()==(p==phase && op!=0?1:0));
      if(p==phase && op!=0) {
        const double total=stats.values[base+VolumeCostStats::total_seconds].load();
        assert(total>0 && total==stats.values[base+VolumeCostStats::legal_seconds].load());
      }
    }
  }
}
'''
with tempfile.TemporaryDirectory() as tmp:
    cpp=Path(tmp)/'repair.cpp';binary=Path(tmp)/'repair'
    cpp.write_text(prefix+body+suffix)
    subprocess.run([os.environ.get('CXX','g++'),'-std=c++17','-Wall','-Wextra','-Werror',
                    '-fsanitize=undefined','-fno-sanitize-recover=all',
                    '-I',str(ROOT.parent/'netgen/libsrc/meshing'),str(cpp),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
print('PASS: production repair forwards both phase identities, preserves defaults and operation order')
