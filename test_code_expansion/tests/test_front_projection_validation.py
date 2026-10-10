#!/usr/bin/env python3
import json,tempfile
from pathlib import Path
from test_incidence_validation import fixture as incidence,profiles
from front_projection_checks import FIELDS,MODES

def fixture(route,repeat,mode='natural'):
 rows=incidence('incidence_ordered',repeat,mode)
 if route=='incidence_ordered':return rows
 for row in rows:
  candidate=route=='projection_fixed'
  row['metadata']['volume_front_projection']=MODES[2] if candidate and repeat==0 else MODES[1] if candidate else MODES[0]
  values=(2,12,3,9,27,9 if candidate else 18,9 if candidate else 0,18,
          2 if candidate and repeat==0 else 0,0,.0001 if candidate else 0,
          .0003,.0005,.0001 if candidate and repeat==0 else 0,4,0)
  row['metrics'].update({'front_projection_'+f:v for f,v in zip(FIELDS,values)})
 return rows

def main():
 with tempfile.TemporaryDirectory() as tmp:
  p=Path(tmp)/'rank_profiles.jsonl'
  def inspect(rows):
   p.write_text(''.join(json.dumps(r)+'\n' for r in rows));return profiles.inspect(p)[0]
  for route in ('incidence_ordered','projection_profile','projection_fixed'):
   for repeat in (0,1):inspect(fixture(route,repeat))
  for field,value in [('verified',0),('mismatches',1),('compiled_planes',0),('derivative_evaluations',8),('iterations',2),('positive_visits',28),('triangles',6),('compile_seconds',.01),('rules_seconds',float('nan')),('common_tests',-1)]:
   bad=fixture('projection_fixed',0);bad[0]['metrics']['front_projection_'+field]=value
   try:inspect(bad)
   except ValueError:pass
   else:raise AssertionError('accepted '+field)
  for field in ('reference_seconds','verified'):
   bad=fixture('projection_fixed',1);bad[0]['metrics']['front_projection_'+field]=1
   try:inspect(bad)
   except ValueError:pass
   else:raise AssertionError('accepted formal '+field)
 print('PASS: projection conservation, full-rank replay, formal isolation and nested timing')
if __name__=='__main__':main()
