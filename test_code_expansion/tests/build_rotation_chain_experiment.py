#!/usr/bin/env python3
"""构建隔离旋转链实验：替换实验链接的 ruler3/meshing3 对象，不改变生产库。"""
import argparse,json,shlex,subprocess
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--build-dir',type=Path,required=True)
p.add_argument('--sanitize',action='store_true',help='仅实验翻译单元启用地址与未定义行为检查')
a=p.parse_args();build=a.build_dir.resolve();repo=Path(__file__).resolve().parents[2]
sanitize=a.sanitize
entries=[e for e in json.loads((build/'compile_commands.json').read_text()) if 'CMakeFiles/nglib.dir/' in e['command']]
rule=next(e for e in entries if e['file'].endswith('/ruler3.cpp'))
flags=shlex.split(rule['command'])
flags.append('-I'+str(repo/'netgen/libsrc/meshing'))
if any(x in flags for x in ('-DPARALLEL','-DOCCGEOMETRY')):raise SystemExit('需无 MPI/OCC 的独立内核构建')
i=flags.index('-o');del flags[i:i+2];flags.remove('-c');flags.remove(rule['file'])
objects=[str(build/shlex.split(e['command'])[shlex.split(e['command']).index('-o')+1])
         for e in entries if not e['file'].endswith(('/ruler3.cpp','/meshing3.cpp'))]
source=(repo/'netgen/libsrc/meshing/meshing3.cpp').read_text()
anchor='      for (int rotind = 1; rotind <= locfaces[0].GetNP(); rotind++)'
assert source.count(anchor)==1
source=source.replace(anchor,"""      auto chain_state=PrepareFrontChain(*this,locpoints,allowpoint,locfaces,locfacesplit,
          connectedpairs,stat.qualclass,mp.sloppy,hshould,rule_mode);
"""+anchor)
a=source.index('          plainpoints.SetSize (locpoints.Size());',source.index('auto chain_state='))
z=source.index('          }',a)
original=source[a:z]
source=source[:a]+"""          if(chain_state) {
            plainpoints=chain_state->input->frames[rotind-1]->points;
            allowpoint=chain_state->input->frames[rotind-1]->allow;
          } else {
"""+original+'          }\n'+source[z:]
anchor='found = ApplyRulesTasks (plainpoints, allowpoint,'
assert source.count(anchor)==1
source=source.replace(anchor,"""if(chain_state) {
              const auto &r=*chain_state->output[rotind-1];
              plainpoints=r.points;locfaces=r.faces;locelements=r.elements;delfaces=r.deleted;
              found=r.found;err=r.error;minother=r.other;minwithoutother=r.without;
              if(front_stats)for(int k=0;k<5;++k)front_stats->AddRule(4+k,r.work[k]);
            } else """+anchor)
(build/'rotation_chain_meshing3.inc').write_text(source)
core=build/'libsrc/core/libngcore.so';output=build/'rotation_chain_experiment'
if sanitize:
    flags+=['-O1','-g1','-fsanitize=address,undefined','-fno-omit-frame-pointer']
    output=build/'rotation_chain_experiment_san'
command=flags+[str(repo/'test_code_expansion/tests/rotation_chain_experiment.cpp')]+objects+[
    str(core),'-lz','-ldl','-pthread','-Wl,-rpath,'+str(core.parent),'-o',str(output)]
subprocess.run(command,check=True,cwd=build)
output.with_suffix('.build.json').write_text(json.dumps(dict(command=command,cwd=str(build)),indent=2)+'\n')
print(output)
