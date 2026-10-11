#!/usr/bin/env python3
"""Production geometry: compare uninstrumented, total-only and stage modes."""
import subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
path=ROOT/'test_code_expansion/tests/test_front_projection_geometry.py'
namespace={'__file__':str(path)}
exec(path.read_text().split('with tempfile.TemporaryDirectory() as tmp:')[0],namespace)
prefix,method,main=[namespace[k] for k in ('prefix','method','main')]
main=main.replace('int b=rule.IsTriangleInFreeSet(p[0],p[1],p[2],1,pi,trial%7!=0,1,candidate);','''int b;
  netgen::VolumeFrontRuleStats stages,totals;
  {FrontRuleCost scope(&stages,1);scope.Switch(netgen::VolumeFrontRuleStats::face_scan_seconds);
   b=rule.IsTriangleInFreeSet(p[0],p[1],p[2],1,pi,trial%7!=0,0,candidate,&scope);}
  auto stage_trace=trace;trace.clear();double coarse[16]={};
  {FrontRuleCost scope(&totals,0);scope.Switch(netgen::VolumeFrontRuleStats::face_scan_seconds);
   assert(a==rule.IsTriangleInFreeSet(p[0],p[1],p[2],1,pi,trial%7!=0,0,coarse,&scope));}
  assert(trace==stage_trace);
  using S=netgen::VolumeFrontRuleStats;
  assert(stages.values[S::shared_triangles]==old[14] && totals.values[S::shared_triangles]==old[14]);
  assert(stages.values[S::unshared_triangles]==1-old[14] && totals.values[S::unshared_triangles]==1-old[14]);
  double sum=0;for(int i=S::prepare_seconds;i<=S::finish_seconds;++i){sum+=stages.values[i];assert(totals.values[i]==0);}
  assert(std::abs(sum-stages.values[S::total_seconds].load())<1e-9);''')
main=main.replace('candidate[5]==candidate[6]','candidate[5]==old[5] && candidate[6]==0')
main=main.replace('int b=rule.IsQuadInFreeZone(p[0],p[1],p[2],p[3],pi,1,1,candidate);','''int b;
  netgen::VolumeFrontRuleStats stages;
  {FrontRuleCost scope(&stages,1);scope.Switch(netgen::VolumeFrontRuleStats::face_scan_seconds);
   b=rule.IsQuadInFreeZone(p[0],p[1],p[2],p[3],pi,1,0,candidate,&scope);}
  using S=netgen::VolumeFrontRuleStats;
  assert(stages.values[S::shared_triangles]==candidate[14]);
  assert(stages.values[S::shared_triangles]+stages.values[S::unshared_triangles]==candidate[1]);''')
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);cpp=tmp/'geometry.cpp';cpp.write_text(prefix+method+main)
    for name,flags in [('checked',['-O2','-fsanitize=undefined','-fno-sanitize-recover=all']),('native',['-O3','-march=native'])]:
        exe=tmp/name
        subprocess.run(['g++','-std=c++17','-I',str(ROOT/'netgen/libsrc/meshing'),*flags,str(cpp),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
