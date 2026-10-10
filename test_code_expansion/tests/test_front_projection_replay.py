#!/usr/bin/env python3
"""执行生产规则回放包装器，拒绝输出/历史状态差异并检查异常恢复。"""
import ast,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
test=ast.parse((ROOT/'test_code_expansion/tests/test_front_distance_replay.py').read_text())
parts={n.targets[0].id:n.value.value for n in test.body if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str)}
prefix=parts['prefix'].replace('int front_projection_mode=0;', 'int front_projection_mode=2;').replace('front_projection_stats=nullptr;', 'front_projection_stats=&projection;')
prefix=prefix.replace('front_distance_mode=2,fault=0','front_distance_mode=0,fault=0')
prefix=prefix.replace('if(front_distance_stats)front_distance_stats->Add(0,1);','if(front_distance_stats)front_distance_stats->Add(0,1);\n  if(front_projection_stats)front_projection_stats->Add(0,1);')
prefix=prefix.replace('if(!front_distance_mode)', 'if(!front_projection_mode)')
main=parts['main'].replace('engine.front_distance_mode==2','engine.front_projection_mode==2 && engine.front_projection_stats==&engine.projection')
main=main.replace('engine.distance.values[8]','engine.projection.values[8]').replace('engine.distance.values[9]','engine.projection.values[9]')
main=main.replace('for(int i : {8,9,10})assert(engine.projection.values[i]==0);',
    'for(int i : {8,9,10})assert(engine.distance.values[i]==0);')
main=main.replace('assert(engine.projection.values[8]',
    'for(int i : {8,9,10})assert(engine.distance.values[i]==0);\n  assert(engine.front_distance_mode==0 && engine.distance.values[0]==1);\n  assert(engine.projection.values[8]')
source=(ROOT/'netgen/libsrc/meshing/ruler3.cpp').read_text()
wrapper=source[source.index('int Meshing3::ApplyRules('):source.index('int Meshing3 :: ApplyRulesImpl')]
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);cpp=tmp/'replay.cpp';exe=tmp/'replay'
    cpp.write_text('#include <cmath>\n'+prefix+wrapper+main)
    subprocess.run(['g++','-std=c++17','-O2','-fsanitize=undefined','-fno-sanitize-recover=all','-Wall','-Wextra','-Werror',str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
