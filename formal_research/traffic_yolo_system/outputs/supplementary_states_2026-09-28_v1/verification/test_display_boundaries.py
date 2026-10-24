"""Production evidence display boundary checks, separate from independent data verifier."""
import copy,hashlib,importlib.util,json,sys
from pathlib import Path
sys.dont_write_bytecode=True
OUT=Path(__file__).resolve().parent;ROOT=OUT.parent
spec=importlib.util.spec_from_file_location('boundary_fixtures',OUT/'test_frozen_boundaries.py')
B=importlib.util.module_from_spec(spec);spec.loader.exec_module(B)
assert hashlib.sha256((ROOT/'code/frozen_engine.py').read_bytes()).hexdigest()==B.ENGINE_SHA
sys.modules['frozen_engine']=B.E
spec=importlib.util.spec_from_file_location('production_derive',ROOT/'code/derive_states.py')
D=importlib.util.module_from_spec(spec);spec.loader.exec_module(D)

def run(windows,t=None):
 windows=copy.deepcopy(windows);t=copy.deepcopy(B.BASE if t is None else t)
 for w in windows:
  w['start_ticks']=round(w['start_s']*90000);w['end_ticks']=round(w['end_s']*90000)
  for r in w['zones']:
   if not r['motion_available']:r['motion_median_px_s']=None
 B.E.classify_windows(windows,t);D.add_display(windows,t);return windows
CHECKS=[]
def check(name,fn):fn();CHECKS.append({'name':name,'passed':True})
def checkval(x):assert x

def degenerate():
 t=copy.deepcopy(B.BASE);t['z'].update(density_low_upper=0,density_high_lower=0)
 r=run([B.W(0,count=0,available=False)],t)[0]['zones'][0]
 assert r['relative_count_level']=='empty' and r['state_hint']=='no_observed_vehicles' and r['evidence_state']=='unknown'
 assert 'count_calibration_degenerate' in r['evidence_unknown_reasons']
check('equal_zero_thresholds_frozen_empty_but_composite_unknown',degenerate)

def degenerate_positive():
 t=copy.deepcopy(B.BASE);t['z'].update(density_low_upper=2,density_high_lower=2)
 r=run([B.W(0,count=3)],t)[0]['zones'][0]
 assert r['relative_count_level']=='unknown' and r['evidence_state']=='unknown' and 'count_calibration_degenerate' in r['evidence_unknown_reasons']
check('equal_thresholds_override_otherwise_available_motion',degenerate_positive)

def motion_missing():
 r=run([B.W(0),B.W(10),B.W(20,available=False)])[-1]['zones'][0]
 assert r['state_hint']=='high_count_persistent' and r['high_count_persistent_count_only'] and r['high_count_duration_s']==30
 assert r['evidence_state']=='unknown' and r['joint_duration_s']==0 and not r['joint_hint']
 assert r['motion_median_px_s'] is None and r['motion_low_relative'] is None
check('motion_missing_keeps_count_only_hint_but_unknown_composite',motion_missing)

def calibration_missing():
 t=copy.deepcopy(B.BASE);t['z'].update(motion_low_upper=-1,motion_calibration_windows=29)
 r=run([B.W(0),B.W(10),B.W(20)],t)[-1]['zones'][0]
 assert r['motion_median_px_s']==1. and r['motion_low_relative'] is None
 assert r['evidence_state']=='unknown' and r['high_count_persistent_count_only'] and not r['joint_hint']
 assert 'motion_calibration_insufficient' in r['evidence_unknown_reasons']
check('29_calibration_samples_keep_absolute_motion_but_relative_null_and_unknown',calibration_missing)

def good():
 rows=[w['zones'][0] for w in run([B.W(0),B.W(10),B.W(20),B.W(30)])]
 assert rows[1]['evidence_state']=='no_persistent_hint'
 assert rows[2]['evidence_state']=='joint_hint' and rows[2]['joint_first_confirmed_here'] and rows[2]['high_count_first_confirmed_here']
 assert rows[2]['joint_hint'] and rows[2]['high_count_persistent_count_only']
 assert rows[3]['evidence_state']=='joint_hint' and not rows[3]['joint_first_confirmed_here'] and not rows[3]['high_count_first_confirmed_here']
check('20_30_seconds_first_confirmation_and_joint_subset',good)

def highonly():
 r=run([B.W(0,motion=2),B.W(10,motion=2),B.W(20,motion=2)])[-1]['zones'][0]
 assert r['evidence_state']=='high_count_hint' and not r['joint_hint'] and r['high_count_persistent_count_only']
check('available_high_motion_allows_count_hint_without_joint_claim',highonly)

def event(kind):
 opts={'held':1} if kind=='held' else {'missing':1} if kind=='missing' else {'duration':9}
 r=run([B.W(0),B.W(10),B.W(20,available=False,complete=False,**opts)])[-1]['zones'][0]
 assert r['high_count_duration_s']==r['joint_duration_s']==0 and r['evidence_state']=='unknown'
 assert {'held':'held_image','missing':'missing_interval','tail':'incomplete_tail'}[kind] in r['evidence_unknown_reasons']
for kind in ('held','missing','tail'):check(kind+'_unknown_and_both_clocks_reset',lambda kind=kind:event(kind))
check('zero_observed_count_never_composite_free_flow',lambda:checkval(run([B.W(0,count=0,available=False)])[0]['zones'][0]['evidence_state']=='unknown'))
check('retrospective_confirmation_time_is_window_end',lambda:checkval(run([B.W(0),B.W(10),B.W(20)])[-1]['confirmation_source_time_s']==30))
result={'passed':True,'check_count':len(CHECKS),'checks':CHECKS,'production_script_sha256':hashlib.sha256((ROOT/'code/derive_states.py').read_bytes()).hexdigest(),'frozen_engine_sha256':B.ENGINE_SHA,'test_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'scope':'actual production add_display boundary cases; synthetic software checks, not traffic accuracy'}
(OUT/'display_boundary_tests.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'passed':True,'check_count':len(CHECKS)}))
