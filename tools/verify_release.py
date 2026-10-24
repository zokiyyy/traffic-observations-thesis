"""Release-only verification helper, added during packaging (not an original experiment).
Uses only packaged files, preserves original reports, writes to a new report directory.
"""
from pathlib import Path
from collections import Counter
import argparse, ast, copy, csv, hashlib, importlib.util, json, math, os, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
OUTPUTS=ROOT/'formal_research/traffic_yolo_system/outputs'
AUDIT=ROOT/'formal_research/12_thesis_draft/revision_2026-10-23/comprehensive_revision_work/roi_count_audit'
def read(p):return json.loads(p.read_text())
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def close(a,b):
 if a is None or b is None:assert a is b,(a,b)
 elif isinstance(a,(float,int)) and isinstance(b,(float,int)):assert math.isclose(a,b,abs_tol=1e-9,rel_tol=1e-10),(a,b)
 else:assert a==b,(a,b)
def counts(rows):
 errors=[int(r['pred_count'])-int(r['gt_count']) for r in rows]
 assert rows
 for r,e in zip(rows,errors):
  close(float(r['error']),e);close(float(r['absolute_error']),abs(e))
 return {'n':len(rows),'MAE':sum(map(abs,errors))/len(rows),'bias':sum(errors)/len(rows),'RMSE':math.sqrt(sum(e*e for e in errors)/len(rows))}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
 ap.add_argument('--require-models',action='store_true',help='Also require both separately distributed model checkpoints. Present models are always hash-checked.')
 args=ap.parse_args()
 out=args.output.resolve()
 if out.exists():raise SystemExit('Output already exists; choose a new folder to preserve reports.')
 # Never allow results under archived project paths.
 if any(p in out.parents or p==out for p in [ROOT/'formal_research',ROOT/'notes']):raise SystemExit('Use a new report folder outside archived records.')
 out.mkdir(parents=True)
 report={'scope':'Local release checks, not original model evaluation or traffic accuracy','checks':{}}
 manifest=read(ROOT/'release_manifest.json')
 for item in manifest['files']:
  p=ROOT/item['path'];assert hashlib.sha256(p.read_bytes()).hexdigest()==item['copied_sha256'],item['path']
 report['checks']['retained_file_hashes']={'passed':True,'files':len(manifest['files'])}
 models=[]
 for item in read(ROOT/'MODEL_MANIFEST.json')['models']:
  p=ROOT/item['path']
  if not p.exists():
   if args.require_models:raise SystemExit('Required model attachment missing: '+item['path'])
   models.append({'path':item['path'],'status':'not included; optional for video-free checks'})
  else:
   assert hashlib.sha256(p.read_bytes()).hexdigest()==item['copied_sha256'],('model hash mismatch',item['path'])
   models.append({'path':item['path'],'status':'present; hash verified'})
 report['checks']['model_attachments']={'required':args.require_models,'files':models}
 parsed=0
 for item in manifest['files']:
  if item['path'].endswith('.py'):
   p=ROOT/item['path'];ast.parse(p.read_text(encoding='utf-8-sig'),filename=item['path']);parsed+=1
 report['checks']['python_syntax']={'passed':True,'files':parsed,'note':'Syntax only; does not establish dependency availability.'}
 with (AUDIT/'main_test/roi_image_errors.csv').open() as f:rows=list(csv.DictReader(f))
 saved=read(AUDIT/'main_test/summary.json');overall=counts(rows)
 for k,v in overall.items():close(v,saved['all_roi_image_metrics'][k])
 assert len(rows)==796 and len({r['image'] for r in rows})==184
 strat=read(AUDIT/'nonempty_stratification.json')['Main Test'];groups={}
 for label,nonzero in [('GT_nonzero',True),('GT_zero',False)]:
  group=[r for r in rows if (int(r['gt_count'])>0)==nonzero];groups[label]=counts(group)
  for k,v in groups[label].items():close(v,strat[label]['overall'][k])
  for camera in {r['camera'] for r in group}:
   m=counts([r for r in group if r['camera']==camera])
   for k,v in m.items():close(v,strat[label]['by_camera'][camera][k])
 for c in saved['by_camera']:
  for k,v in counts([r for r in rows if r['camera']==c['camera']]).items():close(v,c['roi_image_metrics'][k])
 report['checks']['main_test_csv_reaggregation']={'passed':True,'overall':overall,'groups':groups,'limitation':'Does not reopen images/labels, reassign boxes to ROIs or rerun detection.'}
 # Original tests can write reports, so run them in an isolated temporary copy.
 with tempfile.TemporaryDirectory(prefix='traffic-release-check-') as tmp:
  scratch=Path(tmp)/'outputs';scratch.mkdir()
  names=['five_intersection_states_2026-09-28_v1','supplementary_states_2026-09-28_v1','paired_case_comparison_2026-09-28_v1','persistence_sensitivity_2026-09-28']
  for name in names:shutil.copytree(OUTPUTS/name,scratch/name)
  env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
  v=scratch/'supplementary_states_2026-09-28_v1/verification'
  for script,result in [('test_frozen_boundaries.py','boundary_tests.json'),('test_display_boundaries.py','display_boundary_tests.json')]:
   run=subprocess.run([sys.executable,str(v/script)],cwd=scratch,env=env,text=True,capture_output=True)
   assert run.returncode==0,(script,run.stderr[-2000:])
   r=read(v/result);assert r['passed'];report['checks'][script]={'passed':True,'check_count':r['check_count']}
   (out/result).write_text(json.dumps(r,indent=2)+'\n')
  sensitivity=scratch/'persistence_sensitivity_2026-09-28'
  run=subprocess.run([sys.executable,str(sensitivity/'analyze.py')],cwd=scratch,env=env,text=True,capture_output=True)
  assert run.returncode==0,run.stderr[-2000:]
  new=read(sensitivity/'results.json');old=read(OUTPUTS/'persistence_sensitivity_2026-09-28/results.json')
  for key in ['camera_totals','region_summary','thresholds_s']:assert new[key]==old[key],key
  report['checks']['persistence_recalculation']={'passed':True,'region_threshold_rows':len(new['region_summary']),'thresholds_s':new['thresholds_s']}
 # Re-evaluate actual production functions on retained supplementary motion/count windows.
 # This checks numeric outputs; the original full provenance verifier is not bypassed or claimed to have run.
 state=OUTPUTS/'supplementary_states_2026-09-28_v1'
 engine=load('release_frozen_engine',state/'code/frozen_engine.py');sys.modules['frozen_engine']=engine
 derive=load('release_derive',state/'code/derive_states.py')
 totals=Counter();n=0;flags=0;current_high=0
 for case in ['Newport','NE12th','Eastgate','SE38th','NE8th']:
  m=read(OUTPUTS/f'paired_case_comparison_2026-09-28_v1/motion/Supplementary/{case}/motion_10s.json')
  saved=read(state/case/'states_10s.json');windows=copy.deepcopy(m['windows']);zones=[z['id'] for z in m['zones']]
  th=engine.calibrate(windows,zones);engine.classify_windows(windows,th);derive.add_display(windows,th)
  assert len(windows)==len(saved['windows'])
  for z,t in th.items():
   for k,k2 in [('density_low_upper','count_p33'),('density_high_lower','count_p67'),('motion_low_upper','motion_low_upper'),('density_calibration_windows','count_calibration_windows'),('motion_calibration_windows','motion_calibration_windows')]:
    value=t[k];value=None if k=='motion_low_upper' and value<0 else value;close(value,saved['thresholds'][z][k2])
  for w,ref in zip(windows,saved['windows']):
   close(w['confirmation_source_time_s'],ref['confirmation_source_time_s'])
   for r,s in zip(w['zones'],ref['zones']):
    for k in ['zone_id','relative_count_level','state_hint','motion_low_relative','high_count_duration_s','joint_duration_s','high_count_persistent_count_only','joint_hint','evidence_state','evidence_unknown_reasons']:
     close(r[k],s[k])
    n+=1;totals[r['evidence_state']]+=1
    if r['evidence_state']=='unknown':flags+=bool(r['high_count_persistent_count_only']);current_high+=r['relative_count_level']=='high'
 assert n==6342 and totals['unknown']==3798 and flags==41 and current_high==230
 report['checks']['supplementary_production_rule_recalculation']={'passed':True,'region_windows':n,'state_counts':dict(totals),'unknown_persistent_high_count':flags,'unknown_current_high_count':current_high,'limitation':'Uses retained count/motion observations; does not rerun raw video aggregation or validate traffic states.'}
 # Verify no archived record was overwritten by a check.
 for item in manifest['files']:
  assert hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()==item['copied_sha256'],item['path']
 report['retained_records_unchanged']=True;report['passed']=True
 (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report,indent=2))
if __name__=='__main__':main()
