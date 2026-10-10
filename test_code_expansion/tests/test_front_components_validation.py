#!/usr/bin/env python3
import copy,json,tempfile
from pathlib import Path
from test_incidence_validation import fixture as incidence,profiles
from front_component_checks import FIELDS,MODES

def fixture(route,repeat,mode='natural'):
 rows=incidence('incidence_ordered',repeat,mode)
 if route=='incidence_ordered':return rows
 for row in rows:
  candidate=route=='components_union'
  row['metadata']['volume_front_components']=MODES[2] if candidate and repeat==0 else MODES[1] if candidate else MODES[0]
  values=(2,16,8,16 if candidate else 0,10 if candidate else 0,
          0 if candidate and repeat else 6,0 if candidate and repeat else 24,
          2 if candidate and repeat==0 else 0,0,.0002,
          .0001 if candidate and repeat==0 else 0,.0003,.0004,.0003,.0005,4)
  row['metrics'].update({'front_components_'+f:v for f,v in zip(FIELDS,values)})
 return rows

def main():
 with tempfile.TemporaryDirectory() as tmp:
  p=Path(tmp)/'rank_profiles.jsonl'
  def inspect(rows):
   p.write_text(''.join(json.dumps(r)+'\n' for r in rows));return profiles.inspect(p)[0]
  for route in ('incidence_ordered','components_profile','components_union'):
   for repeat in (0,1):inspect(fixture(route,repeat))
  for field,value in [('verified',0),('mismatches',1),('union_attempts',15),('unions',17),('reference_passes',1),('reference_face_visits',7),('rebuilds',5),('label_seconds',.01),('select_seconds',float('nan')),('input_points',-1)]:
   bad=fixture('components_union',0);bad[0]['metrics']['front_components_'+field]=value
   try:inspect(bad)
   except ValueError:pass
   else:raise AssertionError('accepted '+field)
  for field in ('reference_passes','reference_seconds','verified'):
   bad=fixture('components_union',1);bad[0]['metrics']['front_components_'+field]=1
   try:inspect(bad)
   except ValueError:pass
   else:raise AssertionError('accepted formal '+field)
 print('PASS: component counter conservation, full-rank replay, formal isolation and nested timer bounds')
if __name__=='__main__':main()
