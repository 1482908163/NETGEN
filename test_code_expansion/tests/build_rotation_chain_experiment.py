#!/usr/bin/env python3
"""构建隔离旋转链实验：替换实验链接的 ruler3/meshing3 对象，不改变生产库。"""
import argparse,json,shlex,subprocess
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--build-dir',type=Path,required=True)
p.add_argument('--sanitize',action='store_true',help='仅实验翻译单元启用地址与未定义行为检查')
p.add_argument('--ubsan',action='store_true',help='独立未定义行为检查，不依赖地址影子空间')
p.add_argument('--profile',action='store_true',help='独立分项测量构建，不用于正式性能计时')
a=p.parse_args();build=a.build_dir.resolve();repo=Path(__file__).resolve().parents[2]
sanitize=a.sanitize
profile=a.profile
ubsan=a.ubsan
if sum((sanitize,profile,ubsan))>1:raise SystemExit('内存检查、未定义行为检查和分项测量必须分别构建')
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
rule_source=(repo/'netgen/libsrc/meshing/ruler3.cpp').read_text()
anchor='  int Claim(int count) { int i=next.fetch_add(1,std::memory_order_relaxed);'
assert rule_source.count(anchor)==1
rule_source=rule_source.replace(anchor,'''  int Claim(int count) {
    rotation_probe::ClaimTimer claim_timer;
    int i=next.fetch_add(1,std::memory_order_relaxed);
    if constexpr(rotation_probe::enabled)
      if(i<int(order.size()))++rotation_probe::local_claim_successes;''')
anchor='  // static Timer t("ruler3 - all"); RegionTimer reg(t);'
assert rule_source.count(anchor)==1
rule_source=rule_source.replace(anchor,'  rotation_probe::ClaimScope claim_scope;\n'+anchor)
anchor='  // determine topological distance of faces and points to'
assert rule_source.count(anchor)==1
rule_source=rule_source.replace(anchor,'  rotation_probe::PrepareTimer rotation_prepare_timer;\n'+anchor)
anchor='  // check each rule:'
assert rule_source.count(anchor)==1
rule_source=rule_source.replace(anchor,'  rotation_prepare_timer.Finish();\n'+anchor)
# Generate the experimental member from the exact production search body.
# The original ApplyRulesImpl remains intact for routes 0/4 and serial replay.
# Only scratch lifetime, the disabled stream, and single-rule dispatch change.
begin=rule_source.index('int Meshing3 :: ApplyRulesImpl\n')
end=rule_source.rfind('\n}')  # final namespace close; this member is last
workspace_source=rule_source[begin:end]
assert workspace_source.rstrip().endswith('return found;\n}')

def replace_once(source, old, new):
    if source.count(old)!=1:
        raise RuntimeError('旋转链实验源码锚点变化，停止构建：'+old[:80])
    return source.replace(old,new,1)

workspace_source=replace_once(workspace_source,'int Meshing3 :: ApplyRulesImpl\n',
                              'int Meshing3 :: ApplyRulesWorkspace\n')
workspace_source=replace_once(workspace_source,
    'RuleSearchQueue * queue, RuleSearchResult * result\n',
    'RuleSearchQueue * queue, RuleSearchResult * result, RotationChainWorkspace & workspace\n')
workspace_source=replace_once(workspace_source,'  Vector oldu, newu, newu1, newu2, allp;',
    '  auto &oldu=workspace.oldu; auto &newu=workspace.newu; auto &allp=workspace.allp;')
first=workspace_source.index('  NgArray<int, PointIndex::BASE> pused;')
last=workspace_source.index('  int & cnt = RuleDiagnosticCounter();',first)
declarations=workspace_source[first:last]
fields=('pused','fused','pmap','pfixed','fmapi','fmapr','transfreezone','ledges',
        'tempnewpoints','tempnewfaces','tempdelfaces','tempelements','triboxes','pnearness','fnearness')
aliases=''.join('  auto &'+name+'=workspace.'+name+';\n' for name in fields)
workspace_source=workspace_source[:first]+aliases+'''
  // A private workspace is owned by this task for one batch, never concurrently.
  if (!queue || !queue->prepared || !queue->discard_trace || queue->order.size()!=1 ||
      queue->face_end || queue->measure || queue->order[0]<0 || queue->order[0]>=rules.Size())
    throw std::runtime_error("实验工作区只支持已准备的单规则完整搜索");
  // Saved winners are call-local logically; retain capacity, never their values.
  tempnewpoints.SetSize(0);tempnewfaces.SetSize(0);
  tempdelfaces.SetSize(0);tempelements.SetSize(0);
  ++workspace.calls;
'''+workspace_source[last:]
workspace_source=replace_once(workspace_source,'  std::ostream discarded_trace(nullptr);',
    '  auto &discarded_trace=workspace.discarded_trace; discarded_trace.clear();')
workspace_source=replace_once(workspace_source,'''  int serial_rule = 0;
  for (int rim = queue ? queue->Claim(rules.Size()) : serial_rule++;
       rim < rules.Size();
       rim = queue ? queue->Claim(rules.Size()) : serial_rule++)''',
    '''  for (int rim = queue->order[0]; rim < rules.Size(); rim = rules.Size())''')
workspace_struct='''
// Test-only storage. These arrays had been constructed once per rule/rotation.
// Scalar decisions remain local to each call; rule-owned geometry history is
// deliberately not cached or reset here.
struct RotationChainWorkspace {
  unsigned long long calls=0;
  Vector oldu, newu, allp;
'''+declarations.replace('ledges(100)','ledges{100}')+'''
  std::ostream discarded_trace{nullptr};
};
'''
rule_source=rule_source[:end]+workspace_struct+workspace_source+rule_source[end:]
rule_source=replace_once(rule_source,'#include <sstream>','#include <sstream>\n#include <stdexcept>')
(build/'rotation_chain_ruler3.inc').write_text(rule_source)
core=build/'libsrc/core/libngcore.so';output=build/'rotation_chain_experiment'
if sanitize:
    flags+=['-O1','-g1','-fsanitize=address,undefined','-fno-omit-frame-pointer']
    output=build/'rotation_chain_experiment_san'
if ubsan:
    flags+=['-O1','-g1','-fsanitize=undefined','-fno-sanitize-recover=all']
    output=build/'rotation_chain_experiment_ubsan'
if profile:
    flags+=['-DROTATION_CHAIN_PROFILE=1']
    output=build/'rotation_chain_experiment_profile'
command=flags+[str(repo/'test_code_expansion/tests/rotation_chain_experiment.cpp')]+objects+[
    str(core),'-lz','-ldl','-pthread','-Wl,-rpath,'+str(core.parent),'-o',str(output)]
subprocess.run(command,check=True,cwd=build)
output.with_suffix('.build.json').write_text(json.dumps(dict(command=command,cwd=str(build)),indent=2)+'\n')
print(output)
