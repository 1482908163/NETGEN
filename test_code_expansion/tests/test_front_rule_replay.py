#!/usr/bin/env python3
"""Execute the production wrapper, including inactive diagnostic pointers."""
import ast,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
tree=ast.parse((ROOT/'test_code_expansion/tests/test_front_distance_replay.py').read_text())
parts={n.targets[0].id:n.value.value for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str)}
prefix=parts['prefix'].replace('int front_rule_mode=0;','int front_rule_mode=2;').replace('front_rule_stats=nullptr;','front_rule_stats=&rule_stats;')
prefix=prefix.replace('front_distance_mode=2,fault=0','front_distance_mode=0,fault=0')
prefix=prefix.replace('if(front_distance_stats)front_distance_stats->Add(0,1);','if(front_distance_stats)front_distance_stats->Add(0,1);\n  if(front_rule_stats)front_rule_stats->Add(0,1);')
prefix=prefix.replace('if(!front_distance_mode)','if(!front_rule_mode)')
main=parts['main'].replace('engine.front_distance_mode==2','engine.front_rule_mode==2 && engine.front_rule_stats==&engine.rule_stats')
main=main.replace('engine.distance.values[8]','engine.rule_stats.values[16]').replace('engine.distance.values[9]','engine.rule_stats.values[17]')
main=main.replace('assert(engine.rule_stats.values[16]', 'for(int i:{8,9,10})assert(engine.distance.values[i]==0 && engine.transform.values[i]==0 && engine.projection.values[i]==0);\n  assert(engine.rule_stats.values[0]==1);\n  assert(engine.rule_stats.values[16]')
main=main.replace('engine.front_distance_mode=0;engine.front_transform_mode=0;', 'engine.front_distance_mode=0;engine.front_transform_mode=0;engine.front_rule_mode=0;')
main=main.replace('assert(bound.values[10]==1 && bound.values[11]==0);','assert(bound.values[10]==1 && bound.values[11]==0);assert(engine.rule_stats.values[16]==0 && engine.rule_stats.values[17]==0 && engine.rule_stats.values[19]==0);')
source=(ROOT/'netgen/libsrc/meshing/ruler3.cpp').read_text()
wrapper=source[source.index('int Meshing3::ApplyRules('):source.index('int Meshing3 :: ApplyRulesImpl')]
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp);cpp=tmp/'replay.cpp';exe=tmp/'replay'
    cpp.write_text('#include <cmath>\n'+prefix+wrapper+main)
    subprocess.run(['g++','-std=c++17','-O2','-fsanitize=undefined','-fno-sanitize-recover=all','-Wall','-Wextra','-Werror',str(cpp),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
