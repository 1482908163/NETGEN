#!/usr/bin/env python3
"""All-rank construction coverage and exact-replay protocol failures."""
import copy
import json
import tempfile
from pathlib import Path
from test_worklet_routes import fixture as original,profiles
from incidence_checks import PHASES,OPS,FIELDS,MODES


def fixture(route,repeat,mode='natural'):
    rows=original('split_active',repeat,mode)
    if route=='split_active':return rows
    for row in rows:
        candidate=route=='incidence_ordered'
        row['metadata']['volume_incidence_build']=MODES[2] if candidate and repeat==0 else MODES[1] if candidate else MODES[0]
        for phase in PHASES:
            for op in OPS:
                m=row['metrics'];cost=f'cost_{phase}_{op}_';calls=m[cost+'calls'];entries=3*calls
                values=(calls,m[cost+'input_points'],m[cost+'input_elements'],entries,
                        calls if candidate else 0,0,2*entries if candidate else 0,
                        calls if candidate and repeat==0 else 0,0,m[cost+'prepare_seconds']*.2,
                        m[cost+'prepare_seconds']*.2 if candidate and repeat==0 else 0,
                        64 if candidate and calls else 0)
                m.update({f'incidence_{phase}_{op}_{f}':v for f,v in zip(FIELDS,values)})
    return rows


def main():
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'rank_profiles.jsonl'
        def inspect(rows):
            p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            return profiles.inspect(p)[0]
        for route in ('split_active','incidence_profile','incidence_ordered'):
            for repeat in (0,1):inspect(fixture(route,repeat))
        rows=fixture('incidence_ordered',0)
        for field,value in [('calls',0),('entries',1),('verified',0),('mismatches',1),
                            ('ordered_builds',0),('reference_seconds',.1),('build_seconds',.1),
                            ('scratch_peak_bytes',64*1024*1024+1),('input_points',-1),
                            ('input_elements',float('nan')),('fallback_builds',1)]:
            bad=copy.deepcopy(rows);bad[0]['metrics']['incidence_generation_split_'+field]=value
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted invalid incidence '+field)
        for mode in ('unsupported_v1',None,MODES[1]):
            bad=copy.deepcopy(rows);bad[0]['metadata']['volume_incidence_build']=mode
            bad[1]['metadata']=copy.deepcopy(bad[0]['metadata'])
            try:inspect(bad)
            except ValueError:pass
            else:raise AssertionError('accepted invalid incidence protocol')
        fallback=fixture('incidence_ordered',0)
        for row in fallback:
            m=row['metrics']
            for phase in PHASES:
                for op in OPS:
                    prefix=f'incidence_{phase}_{op}_'
                    m[prefix+'fallback_builds']=m[prefix+'ordered_builds'];m[prefix+'ordered_builds']=0
                    m[prefix+'avoided_atomic_updates']=0;m[prefix+'scratch_peak_bytes']=0
        result=inspect(fallback);assert result['incidence_ordered_builds']==0 and result['incidence_fallback_builds']>0
        # Fallback is a valid construction, but route-level coverage rejects a candidate that never ran.
    print('PASS: incidence call/input/timing/memory conservation, warmup verification, formal isolation and fallback protocol')


if __name__=='__main__':main()
