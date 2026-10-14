#!/usr/bin/env python3
"""Review retained exports; does not reconstruct or waive missing raw profiles."""
import argparse,csv,hashlib,json,math,statistics as st,sys
from pathlib import Path

csv.field_size_limit(64*1024*1024)

ROUTES={'multi_control':0,'multi_pack':2,'multi_compact_only':10,'multi_pack_compact':14,'multi_layout':30}
SOURCE='ea28b8a084e1a091b3f8c29ab0b8234fd338a96e'
BINARY='866da58720211bd463dbc28cefbca1a30635c5ba538ccad91fb0454f25573ec2'
CHECKERS={'incidence_checks.py':'e07e8196ac59948a7b710d3747b716797aa6bfe6c2c7e17ed6c2c90e7ed57521','front_workspace_checks.py':'0e9196fcb466b41232a680e4333c82c199d08b44158fca958ce3106b3b5452aa','cost_profile_checks.py':'1ddddaa2d54117c1a5b6c6882acde60e75ee3d007dd252898f25d3f8433f136c'}
WORK=('multi_front_calls','multi_pack_input_elements','multi_pack_selected_elements','multi_pack_send_items','multi_unpack_old_points','multi_unpack_receive_items','multi_unpack_new_points','multi_unpack_vertex_references')
MODES={'volume_front_distance':'distance_original_v1','volume_front_transform':'dense_rule_operator_v1','ghost_transport':'literal_transport_v1','volume_front_workspace':'temporary_workspace_v1','volume_split_evaluation':'active_queue_v1'}

def need(ok,msg):
    if not ok:raise ValueError(msg)

def num(r,k):
    v=float(r[k]);need(math.isfinite(v) and v>=0,('bad value',k,v));return v

def readcsv(p):
    with p.open(newline='') as f:return list(csv.DictReader(f))

def ledger(p,n):
    r=sorted(readcsv(p),key=lambda x:int(x['rank']))
    need([int(x['rank']) for x in r]==list(range(n)),('rank coverage',str(p)))
    return r

def verify_sources(root,manifest_path=None):
    manifest=json.loads((manifest_path or root/'source_manifest.json').read_text())
    manifest={k:v for k,v in manifest.items() if not k.startswith('source/')}
    for name,x in manifest.items():
        b=(root/name).read_bytes()
        need(len(b)==x['size'],('size',name))
        need(hashlib.sha256(b).hexdigest()==x['sha256'],('sha256',name))
        need(hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()==x['git_sha'],('git object',name))
    return manifest

def writecsv(p,rows):
    with p.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def review(root,out,manifest_path=None,scaling_dir=None):
    manifest=verify_sources(root,manifest_path);out.mkdir(parents=True,exist_ok=True)
    startup={}
    for n in (128,256):
        startup[n]=json.loads((root/f'p{n}/mpi_startup/receipt.json').read_text())
        x=startup[n]
        need(x['success'] and x['exit_code']==0 and x['identity']['ranks']==n and x['identity']['binary_sha256']==BINARY,'startup identity')
    need((root/'requested_process_counts.txt').read_text().split()==['128','256'],'requested ranks')
    runtime=json.loads((root/'runtime_manifest.json').read_text())
    need(runtime['source_revision']==SOURCE,'runtime source')
    if scaling_dir is None:
        local=root/'source/test_code_expansion/strong_scaling'
        scaling_dir=local if local.is_dir() else Path(__file__).resolve().parents[2]/'test_code_expansion/strong_scaling'
    for name,expected in CHECKERS.items():
        need(hashlib.sha256((scaling_dir/name).read_bytes()).hexdigest()==expected,('checker source changed',name))
    sys.path.insert(0,str(scaling_dir))
    import incidence_checks,front_workspace_checks
    from cost_profile_checks import TRANSFORM_FIELDS
    data={};quality={};critical={};totals={'algorithm_certificates':0,'multi_certificates':0,'rank_records_per_family':0,'front_verified':0,'pack_verified_elements':0,'unpack_verified_items':0,'shared_count_comparisons':0,'multi_work_comparisons':0}
    modes=set(MODES)|{'volume_repair_fixedpoint','volume_incidence_build'}
    for n in (128,256):
        plan={'ranks':n,'repeats':3,'algorithms':['sparse'],'timings':['natural'],'partition_seeds':[-1,17,41],'quality_warmup':True}
        for route,mask in ROUTES.items():
            rd=root/f'route_{route}/p{n}'
            config=(rd/'configuration.txt').read_text().splitlines()
            need(config[:5]==[str(n),'sparse','natural','3','1'],('configuration header',n,route))
            for key,value in {'--multi-point':str(mask),'--kernel-threads':'4','-l':'2','-r':'2','--maxh':'1000','--minh':'0','--ghost-exchange':'legacy'}.items():
                need(config.count(key)==1 and config[config.index(key)+1]==value,('configuration option',n,route,key))
            for setting in ('PARTITION_SEEDS=-1 17 41','PLACEMENT_ROTATION=0','RANKS_PER_NODE=4','CPUS_PER_TASK=4','KERNEL_ORDER=interleaved_v2','QUALITY_WARMUP=1','SOURCE_REVISION='+SOURCE):
                need(setting in config,('configuration setting',n,route,setting))
            need(json.loads((rd/'plan.json').read_text())==plan,('plan',n,route))
            with (rd/'run_status.tsv').open(newline='') as f:status=list(csv.DictReader(f,delimiter='\t'))
            need(len(status)==12 and len({(r['partition_seed'],r['repeat']) for r in status})==12 and all(r['exit_code']=='0' for r in status),('status',n,route))
            need((rd/'analysis/issues.txt').read_text().strip()=='',('issues',n,route))
            critical[n,route]=readcsv(rd/'analysis/critical_rank_breakdown.csv')
            for seed in (-1,17,41):
                stem='sparse'+('' if seed==-1 else f'_seed{seed}')+'_natural'
                qpath=rd/stem/'repeat_0/quality_summary.json';q=json.loads(qpath.read_text())
                need(q['structural_pass'] and not q['issues'] and q['counts']['hard_errors']==0,('quality',n,seed,route))
                need([int(x['rank']) for x in q['per_rank']]==list(range(n)),('quality ranks',n,seed,route))
                need(q['identity']['MESH_SOURCE_REVISION']==SOURCE and q['identity']['MESH_BINARY_SHA256']==BINARY,('quality identity',n,seed,route))
                need(q['identity']['MESH_KERNEL_SHA256']=='35441184233f44b6858e5ffd49a8e227a88c952564c878c1c9860b22f0d7a607' and q['identity']['MESH_INPUT_SHA256']=='8193db1235a9d0dc24d19dd3b9a4dafccfe62d6667ab5a395f72b30a02339547',('kernel/input',route,seed))
                quality[n,seed,route]=q
                if route!='multi_control':need(qpath.read_bytes()==(root/f'route_multi_control/p{n}'/stem/'repeat_0/quality_summary.json').read_bytes(),('complete quality difference',n,seed,route))
                for rep in range(4):
                    d=rd/stem/f'repeat_{rep}';a=ledger(d/'algorithm_certificate.csv',n);m=ledger(d/'multi_point_certificate.csv',n)
                    totals['algorithm_certificates']+=1;totals['multi_certificates']+=1;totals['rank_records_per_family']+=n
                    for rank,(x,y) in enumerate(zip(a,m)):
                        for row in (x,y):need(row['source_revision']==SOURCE and row['binary_sha256']==BINARY and int(row['ranks'])==n and int(row['repeat'])==rep,('identity',n,seed,route,rep,rank))
                        need(y['algorithm']=='sparse' and y['timing']=='natural' and int(y['partition_seed'])==seed and int(y['multi_point_mask'])==mask and y['multi_point_policy']=='exact_local_work_v1',('multi route',n,seed,route,rep,rank))
                        need(y['multi_point_pack_layout']==('csr_exact_pack_v1' if mask&8 else 'dense_peer_vectors_v1' if mask&2 else 'legacy_pack_v1'),('pack layout',route,seed,rep,rank))
                        need(y['multi_point_unpack_layout']==('flat_gid_index_v1' if mask&16 else 'node_hash_index_v1' if mask&4 else 'legacy_unpack_v1'),('unpack layout',route,seed,rep,rank))
                        need(y['multi_point_verification']==('whole_reference_v1' if rep==0 else 'disabled_v1'),('multi verification',n,seed,route,rep,rank))
                        for k,v in x.items():
                            if k not in modes and k not in ('source_revision','binary_sha256'):
                                z=num(x,k)
                                if 'seconds' not in k:need(z.is_integer() and z<=2**53,('count',k,z))
                        for k in y:
                            if k.startswith('multi_') and not k.startswith('multi_point_'):
                                z=num(y,k)
                                if 'seconds' not in k:need(z.is_integer() and z<=2**53,('multi count',k,z))
                        for k,v in MODES.items():
                            expected_mode='active_exact_replay_v1' if rep==0 and k=='volume_split_evaluation' else v
                            need(x[k]==expected_mode,('mode',k,x[k]))
                        need(x['volume_repair_fixedpoint']==('reference_verify_v1' if rep==0 else 'exact_state_stop_v1'),('repair mode',x['volume_repair_fixedpoint']))
                        need(x['volume_incidence_build']==('ordered_incidence_replay_v1' if rep==0 else 'ordered_incidence_v1'),('incidence mode',x['volume_incidence_build']))
                        need(num(y,'core_seconds')>0 and num(y,'local_core_seconds')>0,('core',n,seed,route,rep,rank))
                        need(num(y,'volume_exchange_full_seconds')==num(x,'ghost_transport_full_seconds') and num(y,'volume_exchange_full_seconds')<=num(y,'local_core_seconds')+1e-3,('full exchange timing',n,seed,route,rep,rank))
                        qp=q['per_rank'][rank]
                        need(num(y,'multi_pack_calls')==num(y,'multi_unpack_calls')==1,('pack/unpack calls',n,seed,route,rep,rank))
                        need(num(y,'multi_pack_input_elements')==qp['elements'] and num(y,'multi_unpack_old_points')==qp['points'],('owned mesh coverage',n,seed,route,rep,rank))
                        need(num(y,'multi_unpack_receive_items')==qp['adjacency_ghosts'] and num(y,'multi_unpack_new_points')+num(y,'multi_unpack_old_points')==qp['adjacency_points'],('adjacency coverage',n,seed,route,rep,rank))
                        need(num(y,'multi_pack_selected_elements')<=num(y,'multi_pack_candidate_elements')<=num(y,'multi_pack_input_elements'),('candidate accounting',n,seed,route,rep,rank))
                        need(num(y,'multi_unpack_vertex_references')==4*num(y,'multi_unpack_receive_items'),('vertex references',n,seed,route,rep,rank))
                        need(num(y,'multi_pack_send_items')==num(x,'ghost_transport_send_items') and num(y,'multi_unpack_receive_items')==num(x,'ghost_transport_receive_items'),('independent ghost counts',n,seed,route,rep,rank))
                        need(num(x,'ghost_transport_raw_send_bytes')==140*num(x,'ghost_transport_send_items') and num(x,'ghost_transport_raw_receive_bytes')==140*num(x,'ghost_transport_receive_items'),('wire bytes',n,seed,route,rep,rank))
                        for side in ('send','receive'):
                            need(num(x,f'ghost_transport_payload_{side}_bytes')==num(x,f'ghost_transport_raw_{side}_bytes'),('literal payload',side))
                            need(num(x,f'ghost_transport_metadata_{side}_bytes')==num(x,f'ghost_transport_metadata_{side}_messages')==num(x,f'ghost_transport_dictionary_{side}_peers')==0,('unexpected dictionary',side))
                        need(num(y,'multi_front_mismatches')==0,('front mismatch',n,seed,route,rep,rank))
                        expected=((1,'multi_front_verified','multi_front_calls'),(2,'multi_pack_verified_elements','multi_pack_input_elements'),(4,'multi_unpack_verified_items','multi_unpack_receive_items'))
                        for bit,k,work in expected:
                            need(num(y,k)==(num(y,work) if rep==0 and mask&bit else 0),('reference coverage',n,seed,route,rep,rank,k))
                        if not mask&1:need(all(num(y,'multi_front_'+k)==0 for k in ('refreshed_faces','dirty_blocks','visited_blocks','rebuilds','indexed_faces','point_links')),('disabled index',n,seed,route,rep,rank))
                        for k in x:
                            if k.endswith('_mismatches'):need(num(x,k)==0,('shared mismatch',k))
                            if rep>0 and (k.endswith('_verified') or k.endswith('_reference_seconds') or k.endswith('_reference_rounds')):need(num(x,k)==0,('formal shared reference',k))
                        if rep>0:
                            for g in ('front','pack','unpack'):need(num(y,f'multi_{g}_reference_seconds')==0,('formal reference',g))
                        else:
                            totals['front_verified']+=num(y,'multi_front_verified');totals['pack_verified_elements']+=num(y,'multi_pack_verified_elements');totals['unpack_verified_items']+=num(y,'multi_unpack_verified_items')
                            need(num(x,'split_active_verified')==num(x,'split_active_edges'),('active reference coverage',rank))
                            for phase in ('generation','repair','optimization'):
                                need(num(x,f'repair_fixed_{phase}_verified')==num(x,f'repair_fixed_{phase}_stable_rounds'),('repair reference coverage',phase,rank))
                        need(num(x,'split_active_active')==num(x,'split_active_dispatched') and num(x,'split_active_active')+num(x,'split_active_rejected')==num(x,'split_active_edges'),('active work',rank))
                        for phase in ('generation','repair','optimization'):
                            for op in ('combine','split','swap','swap2'):
                                pre=f'incidence_{phase}_{op}_'
                                need(num(x,pre+'calls')==num(x,pre+'ordered_builds')+num(x,pre+'fallback_builds'),('incidence calls',pre))
                                need(num(x,pre+'avoided_atomic_updates')==2*num(x,pre+'entries') and num(x,pre+'fallback_builds')==0,('incidence work',pre))
                                need(num(x,pre+'verified')==(num(x,pre+'calls') if rep==0 else 0),('incidence reference coverage',pre))
                    need(len({x['core_seconds'] for x in m})==1,('global core consistency',n,seed,route,rep))
                    need(abs(max(num(x,'local_core_seconds') for x in m)-num(m[0],'core_seconds'))<1e-8,('global core maximum',n,seed,route,rep))
                    need(sum(num(x,'ghost_transport_send_items') for x in a)==sum(num(x,'ghost_transport_receive_items') for x in a),('global ghost conservation',n,seed,route,rep))
                    data[n,seed,rep,route]=(a,m)
                    if route!='multi_control':
                        ba,bm=data[n,seed,rep,'multi_control']
                        for rank,(x,b,y,by) in enumerate(zip(a,ba,m,bm)):
                            for k in b:
                                if 'seconds' not in k:
                                    need(x[k]==b[k],('shared per-rank work changed',n,seed,rep,route,rank,k));totals['shared_count_comparisons']+=1
                            for k in WORK:need(y[k]==by[k],('multi per-rank work changed',n,seed,rep,route,rank,k));totals['multi_work_comparisons']+=1
    # Retained run tables contain all common per-rank signatures. Recheck
    # against certificates where independently exported, and compare every
    # required work signature without manufacturing a missing raw profile.
    required_signatures=('multi_point_work_signature','ghost_transport_work_signature','ghost_payload_coverage_signature','front_workspace_work_signature','incidence_work_signature','front_distance_work_signature','cost_work_signature','front_transform_work_signature')
    run_tables={};signature_checks=0
    for n in (128,256):
        for route in ROUTES:
            rows=readcsv(root/f'route_{route}/p{n}/analysis/runs.csv')
            need(len(rows)==9,('run table completeness',n,route))
            table={}
            for row in rows:
                run={}
                for k,v in row.items():
                    try:run[k]=float(v)
                    except (ValueError,TypeError):run[k]=v
                seed,rep=int(run['partition_seed']),int(run['repeat']);key=(seed,rep)
                need(key not in table and seed in (-1,17,41) and rep in (1,2,3),('run table key',n,route,key))
                need((run['source_revision'],run['binary_sha256'],run['algorithm'],run['timing'],int(run['ranks']),int(run['multi_point_mask']))==(SOURCE,BINARY,'sparse','natural',n,ROUTES[route]),('run table identity',n,route,key))
                a,m=data[n,seed,rep,route]
                need(all(run[k]==m[0][k] for k in ('multi_point_pack_layout','multi_point_unpack_layout')),('run/certificate layout',route,seed,rep))
                need(run['core_seconds']==num(m[0],'core_seconds'),('core table/certificate differs',n,route,key))
                for fields,signature in ((WORK,'multi_point_work_signature'),(tuple('ghost_transport_'+f for f in ('send_items','receive_items','raw_send_bytes','raw_receive_bytes')),'ghost_transport_work_signature'),(tuple('front_distance_'+f for f in ('calls','queries','rules','candidates')),'front_distance_work_signature')):
                    signature_rows=m if signature.startswith('multi_point') else a
                    expected=[[i]+[num(r,k) for k in fields] for i,r in enumerate(signature_rows)]
                    need(expected==json.loads(run[signature]),('table/certificate work signature',n,route,key,signature))
                incidence_checks.check_certificates(a,run,rep,n,'ordered_incidence_v1')
                front_workspace_checks.check_certificates(a,run,rep,n,'temporary_workspace_v1')
                expect=[[i]+[num(r,'front_transform_'+k) for k in TRANSFORM_FIELDS] for i,r in enumerate(a)]
                need(expect==json.loads(run['front_transform_certificate_signature']),('transform certificate',n,route,key))
                payload=json.loads(run['ghost_payload_coverage_signature'])
                need(len(payload)==n and [r[0] for r in payload]==list(range(n)),('payload ranks',n,route,key))
                for rank,pr in enumerate(payload):
                    need(pr[:5]==[rank]+[num(a[rank],'ghost_transport_'+k) for k in ('send_items','receive_items','literal_send_peers','literal_receive_peers')],('payload/certificate',n,route,key,rank))
                for k in m[0]:
                    if k.startswith('multi_') and not k.startswith('multi_point_'):
                        expected=sum(num(r,k) for r in m)
                        need(math.isclose(expected,run[k],rel_tol=1e-12,abs_tol=1e-9),('multi aggregate',n,route,key,k))
                table[key]=run
                if route!='multi_control':
                    control=run_tables[n,'multi_control'][key]
                    for signature in required_signatures:
                        need(json.loads(run[signature])==json.loads(control[signature]),('shared signature differs',n,route,key,signature));signature_checks+=1
            run_tables[n,route]=table
    # Parse critical exports without replacing an absent rank by another rank.
    cdict={}
    for (n,route),rows in critical.items():
        for r in rows:
            key=(n,int(r['partition_seed']),int(r['repeat']),route,int(r['rank']))
            need(key not in cdict,('critical duplicate',key));cdict[key]=r
    pairs=[];rank_pairs=[]
    measures={'local_core_seconds':'same_core','local_volume_mesh_seconds':'same_volume_mesh','volume_exchange_full_seconds':'same_exchange','multi_front_selection_seconds':'same_select','multi_pack_seconds':'same_pack','multi_unpack_seconds':'same_unpack'}
    for n in (128,256):
        for seed in (-1,17,41):
            for rep in (1,2,3):
                ba,bm=data[n,seed,rep,'multi_control']
                core_rank=max(range(n),key=lambda i:num(bm[i],'local_core_seconds'))
                exchange_rank=max(range(n),key=lambda i:num(bm[i],'volume_exchange_full_seconds'))
                compute_rows=[r for r in critical[n,'multi_control'] if int(r['partition_seed'])==seed and int(r['repeat'])==rep and int(r['compute_order'])==1]
                need(len(compute_rows)==1,('compute critical coverage',n,seed,rep));compute_rank=int(compute_rows[0]['rank'])
                for route in ROUTES:
                    if route=='multi_control':continue
                    a,m=data[n,seed,rep,route];x=bm[core_rank];y=m[core_rank]
                    br=run_tables[n,'multi_control'][seed,rep];yrun=run_tables[n,route][seed,rep]
                    row=dict(ranks=n,seed=seed,repeat=rep,route=route,control_core_rank=core_rank,control_exchange_rank=exchange_rank,control_compute_rank=compute_rank,control_core_seconds=num(bm[0],'core_seconds'),candidate_core_seconds=num(m[0],'core_seconds'),control_compute_imbalance=br['compute_imbalance'],candidate_compute_imbalance=yrun['compute_imbalance'],compute_global_max_reduction_percent=100*(1-yrun['compute_max_seconds']/br['compute_max_seconds']))
                    row['core_reduction_percent']=100*(1-row['candidate_core_seconds']/row['control_core_seconds'])
                    for k,name in measures.items():
                        row[name+'_control_seconds']=num(x,k);row[name+'_candidate_seconds']=num(y,k)
                        row[name+'_saved_ms']=1000*(num(x,k)-num(y,k))
                        row[name+'_reduction_percent']=100*(1-num(y,k)/num(x,k)) if num(x,k)>0 else None
                    row['exchange_fixed_rank_reduction_percent']=100*(1-num(m[exchange_rank],'volume_exchange_full_seconds')/num(bm[exchange_rank],'volume_exchange_full_seconds'))
                    row['exchange_global_max_reduction_percent']=100*(1-max(num(r,'volume_exchange_full_seconds') for r in m)/max(num(r,'volume_exchange_full_seconds') for r in bm))
                    row['rule_fixed_core_rank_reduction_percent']=100*(1-num(a[core_rank],'front_distance_apply_seconds')/num(ba[core_rank],'front_distance_apply_seconds')) if num(ba[core_rank],'front_distance_apply_seconds')>0 else None
                    cr=cdict.get((n,seed,rep,'multi_control',compute_rank));yr=cdict.get((n,seed,rep,route,compute_rank))
                    row['same_compute_rank_coverage']=yr is not None
                    row['same_compute_rank_reduction_percent']=100*(1-num(yr,'compute_seconds')/num(cr,'compute_seconds')) if yr else None
                    row['candidate_compute_rank']=int(next(r for r in critical[n,route] if int(r['partition_seed'])==seed and int(r['repeat'])==rep and int(r['compute_order'])==1)['rank'])
                    pairs.append(row)
                    for rank,(x,y) in enumerate(zip(bm,m)):
                        rank_pairs.append(dict(ranks=n,seed=seed,repeat=rep,route=route,rank=rank,pack_control=num(x,'multi_pack_seconds'),pack_candidate=num(y,'multi_pack_seconds'),unpack_control=num(x,'multi_unpack_seconds'),unpack_candidate=num(y,'multi_unpack_seconds'),select_control=num(x,'multi_front_selection_seconds'),select_candidate=num(y,'multi_front_selection_seconds'),score_control=num(x,'multi_front_score_reads'),score_candidate=num(y,'multi_front_score_reads'),input_elements=num(y,'multi_pack_input_elements'),candidate_elements=num(y,'multi_pack_candidate_elements')))
    summaries=[]
    for n in (128,256):
        for route in list(ROUTES)[1:]:
            for seed in (-1,17,41):
                g=[r for r in pairs if (r['ranks'],r['seed'],r['route'])==(n,seed,route)]
                z=dict(ranks=n,seed=seed,route=route,pairs=len(g),core_wins=sum(r['core_reduction_percent']>0 for r in g))
                for key in ('core_reduction_percent','same_core_reduction_percent','same_volume_mesh_reduction_percent','same_exchange_reduction_percent','same_pack_reduction_percent','same_unpack_reduction_percent','same_select_reduction_percent','same_pack_saved_ms','same_unpack_saved_ms','same_select_saved_ms','exchange_fixed_rank_reduction_percent','exchange_global_max_reduction_percent','rule_fixed_core_rank_reduction_percent','same_compute_rank_reduction_percent'):
                    v=[r[key] for r in g if r[key] is not None];z[key+'_median']=st.median(v) if v else None
                z['same_compute_rank_coverage']=sum(r['same_compute_rank_coverage'] for r in g);summaries.append(z)
    mechanisms=[]
    for n in (128,256):
        for route in list(ROUTES)[1:]:
            rs=[r for r in rank_pairs if (r['ranks'],r['route'])==(n,route)]
            z=dict(ranks=n,route=route,rank_pairs=len(rs))
            for what in ('pack','unpack','select'):
                gs=[r for r in rs if r[what+'_control']>0]
                z[what+'_wins']=sum(r[what+'_candidate']<r[what+'_control'] for r in gs)
                z[what+'_reduction_percent_median']=st.median(100*(1-r[what+'_candidate']/r[what+'_control']) for r in gs)
            z['score_read_ratio']=sum(r['score_candidate'] for r in rs)/sum(r['score_control'] for r in rs)
            z['candidate_fraction']=sum(r['candidate_elements'] for r in rs)/sum(r['input_elements'] for r in rs)
            mechanisms.append(z)
    incremental=[];incremental_mechanisms=[]
    comparisons=(('multi_compact_only','multi_pack'),('multi_pack_compact','multi_compact_only'),('multi_layout','multi_compact_only'),('multi_layout','multi_pack'),('multi_layout','multi_pack_compact'))
    for n in (128,256):
        for candidate,baseline in comparisons:
            rank_values={'pack':[],'unpack':[]};wins={'pack':0,'unpack':0}
            for seed in (-1,17,41):
                for rep in (1,2,3):
                    bm=data[n,seed,rep,baseline][1];cm=data[n,seed,rep,candidate][1]
                    old=max(range(n),key=lambda i:num(bm[i],'local_core_seconds'));new=max(range(n),key=lambda i:num(cm[i],'local_core_seconds'))
                    row=dict(ranks=n,seed=seed,repeat=rep,baseline=baseline,candidate=candidate,baseline_core_seconds=num(bm[0],'core_seconds'),candidate_core_seconds=num(cm[0],'core_seconds'),baseline_core_rank=old,candidate_core_rank=new)
                    row['core_reduction_percent']=100*(1-row['candidate_core_seconds']/row['baseline_core_seconds'])
                    for key in ('local_core_seconds','local_volume_mesh_seconds','volume_exchange_full_seconds','multi_pack_seconds','multi_unpack_seconds'):
                        for which,rank in (('baseline_rank',old),('candidate_rank',new)):
                            row[which+'_baseline_'+key]=num(bm[rank],key);row[which+'_candidate_'+key]=num(cm[rank],key)
                    br=run_tables[n,baseline][seed,rep];cr=run_tables[n,candidate][seed,rep]
                    for key in ('compute_max_seconds','compute_imbalance'):
                        row['baseline_'+key]=br[key];row['candidate_'+key]=cr[key]
                    incremental.append(row)
                    for x,y in zip(bm,cm):
                        for what in rank_values:
                            key='multi_'+what+'_seconds'
                            if num(x,key)>0:rank_values[what].append(100*(1-num(y,key)/num(x,key)));wins[what]+=num(y,key)<num(x,key)
            z=dict(ranks=n,candidate=candidate,baseline=baseline)
            for what in rank_values:z.update({what+'_rank_pairs':len(rank_values[what]),what+'_wins':wins[what],what+'_reduction_percent_median':st.median(rank_values[what])})
            incremental_mechanisms.append(z)
    writecsv(out/'multi_layout_followup_incremental.csv',incremental);writecsv(out/'multi_layout_followup_incremental_mechanisms.csv',incremental_mechanisms)
    writecsv(out/'multi_layout_followup_pairs.csv',pairs);writecsv(out/'multi_layout_followup_summary.csv',summaries);writecsv(out/'multi_layout_followup_mechanisms.csv',mechanisms)
    comparison_summary=[]
    for n in (128,256):
        comparisons_all=[(r,'multi_control') for r in list(ROUTES)[1:]]+list(comparisons)
        for candidate,baseline in comparisons_all:
            rows=[r for r in (pairs if baseline=='multi_control' else incremental) if r['ranks']==n and r.get('route',r.get('candidate'))==candidate and (baseline=='multi_control' or r['baseline']==baseline)]
            values=[r['core_reduction_percent'] for r in rows]
            need(len(values)==9,('comparison coverage',n,candidate,baseline))
            comparison_summary.append(dict(ranks=n,candidate=candidate,baseline=baseline,pairs=len(values),wins=sum(v>0 for v in values),core_reduction_percent_median=st.median(values),core_reduction_percent_min=min(values),core_reduction_percent_max=max(values)))
    writecsv(out/'multi_layout_followup_comparisons.csv',comparison_summary)
    evidence=dict(source_revision=SOURCE,upload_revision='7423f74560617c0e1f8dced59b38536d513b37d0',source_files=len(manifest),checker_sources=CHECKERS,shared_signature_comparisons=signature_checks,audit_scope='retained exports; no raw profile reconstruction',raw_profile_revalidated=False,totals=totals,pairs=pairs,summaries=summaries,mechanisms=mechanisms)
    evidence.update(startup=startup,incremental=incremental,incremental_mechanisms=incremental_mechanisms,comparison_summary=comparison_summary)
    (out/'multi_layout_followup_evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    (out/'multi_layout_followup_sources.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'totals':totals,'summaries':summaries,'mechanisms':mechanisms},ensure_ascii=False,indent=2))
    return evidence

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--source-manifest',type=Path);p.add_argument('--scaling-dir',type=Path);a=p.parse_args();review(a.root,a.output,a.source_manifest,a.scaling_dir)
