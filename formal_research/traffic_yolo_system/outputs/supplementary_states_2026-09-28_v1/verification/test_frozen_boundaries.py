"""Synthetic boundary checks of the byte-identical frozen state functions.
No videos, raw frame logs, model or Main Test data are opened.
"""
from __future__ import annotations
import copy, hashlib, importlib.util, json, sys
from pathlib import Path
sys.dont_write_bytecode=True
OUT=Path(__file__).resolve().parent
OUTPUTS=OUT.parents[1]
ENGINE=OUTPUTS/'five_intersection_states_2026-09-28_v1/code/engine.py'
ENGINE_SHA='1ca1c8f7eb024e99b78e96cb72c2adbd545ba61635c950f7d70fdf0da5062390'
assert hashlib.sha256(ENGINE.read_bytes()).hexdigest()==ENGINE_SHA
spec=importlib.util.spec_from_file_location('frozen_state_engine',ENGINE)
E=importlib.util.module_from_spec(spec);spec.loader.exec_module(E)
BASE={'z':{'density_low_upper':1.,'density_high_lower':2.,'motion_low_upper':1.,'density_calibration_windows':100,'motion_calibration_windows':100}}

def W(start,count=3,motion=1.,available=True,complete=True,duration=10.,held=0.,missing=0.):
 return {'index':int(start//10),'start_s':float(start),'end_s':float(start+duration),'complete':complete,'full_valid_window':duration==10 and missing==0,'held_duration_s':held,'zones':[{'zone_id':'z','count_mean':count,'motion_median_px_s':motion if available else -1.,'motion_available':available,'motion_coverage':.8 if available else 0.,'motion_vehicle_seconds':.8*max(count,0)*(duration-missing) if available else 0.,'observed_vehicle_seconds':max(count,0)*(duration-missing),'valid_duration_s':duration-missing,'missing_duration_s':missing}]}

def classify(windows,thresholds=BASE):
 windows=copy.deepcopy(windows);E.classify_windows(windows,copy.deepcopy(thresholds));return [w['zones'][0] for w in windows]

CHECKS=[]
def check(name,fn):
 fn();CHECKS.append({'name':name,'passed':True})
def require(cond):assert cond

check('linear_interpolation_P33_P67_P25',lambda: require(E.calibrate([W(i*10,count=i,motion=i) for i in range(100)],['z'])['z']=={'density_low_upper':32.67,'density_high_lower':66.33,'motion_low_upper':24.75,'density_calibration_windows':100,'motion_calibration_windows':100}))
check('29_motion_calibration_windows_keep_P25_unavailable',lambda: require(E.calibrate([W(i*10,count=i,motion=i) for i in range(29)],['z'])['z']['motion_low_upper']==-1.))
check('30_motion_calibration_windows_enable_P25',lambda: require(E.calibrate([W(i*10,count=i,motion=i) for i in range(30)],['z'])['z']['motion_low_upper']==7.25))

def excluded():
 rows=[W(i*10,count=i,motion=i) for i in range(30)]
 rows.extend([W(300,count=1000,motion=1000,complete=False,held=1),W(310,count=1000,motion=1000,complete=False,missing=1),W(320,count=1000,motion=1000,complete=False,duration=9)])
 t=E.calibrate(rows,['z'])['z'];assert t['density_calibration_windows']==30 and t['motion_calibration_windows']==30 and t['motion_low_upper']==7.25
check('held_missing_tail_excluded_from_calibration',excluded)

check('equal_count_thresholds_positive_count_unknown',lambda: require(E.density_level(2.,2.,2.)=='unknown'))
check('equal_count_thresholds_zero_observation_keeps_frozen_empty',lambda: require(E.density_level(0.,0.,0.)=='empty'))
check('count_threshold_boundary_inclusive_low_and_middle',lambda: require([E.density_level(x,1.,2.) for x in (0.,1.,2.,2.000000001)]==['empty','low','medium','high']))

def persist():
 r=classify([W(0),W(10),W(20)])
 assert [x['joint_duration_s'] for x in r]==[10.,20.,30.]
 assert r[1]['traffic_state']=='no_persistent_signal'
 assert r[2]['traffic_state']=='high_count_low_motion_persistent'
check('20_seconds_not_persistent_30_seconds_persistent',persist)
check('motion_P25_equality_counts_as_relative_low',lambda: require(classify([W(0,motion=1),W(10,motion=1),W(20,motion=1)])[-1]['joint_duration_s']==30.))

def reset_event(kind):
 extra={'held':1} if kind=='held' else {'missing':1} if kind=='missing' else {'duration':9}
 event=W(20,complete=False,available=False,**extra)
 following=event['end_s']
 r=classify([W(0),W(10),event,W(following)])
 assert r[2]['high_count_duration_s']==r[2]['joint_duration_s']==0.
 assert r[2]['traffic_state']=='insufficient_window'
 assert r[3]['high_count_duration_s']==r[3]['joint_duration_s']==10.
for kind in ('held','missing','tail'):
 check(kind+'_resets_both_persistence_clocks',lambda kind=kind:reset_event(kind))

check('noncontiguous_PTS_resets_both_clocks',lambda: require(classify([W(0),W(10),W(30)])[-1]['high_count_duration_s']==classify([W(0),W(10),W(30)])[-1]['joint_duration_s']==10.))

def motion_gap():
 r=classify([W(0),W(10),W(20,available=False),W(30)])
 assert r[2]['high_count_duration_s']==30. and r[2]['joint_duration_s']==0.
 assert r[2]['traffic_state']=='high_count_persistent'
 assert r[3]['high_count_duration_s']==40. and r[3]['joint_duration_s']==10.
check('motion_missing_only_resets_joint_clock_count_only_can_persist',motion_gap)

def low_motion_break():
 r=classify([W(0),W(10),W(20,motion=1.00001)])[-1]
 assert r['high_count_duration_s']==30. and r['joint_duration_s']==0. and r['traffic_state']=='high_count_persistent'
check('motion_above_P25_breaks_joint_not_count_clock',low_motion_break)

def absent_calibration():
 t=copy.deepcopy(BASE);t['z']['motion_low_upper']=-1;t['z']['motion_calibration_windows']=29
 r=classify([W(0),W(10),W(20)],t)[-1]
 assert r['high_count_duration_s']==30. and r['joint_duration_s']==0. and r['traffic_state']=='high_count_persistent'
check('absent_P25_blocks_joint_but_frozen_count_only_can_persist',absent_calibration)

def no_observation():
 r=classify([W(0),W(10),W(20,count=0,available=False)])[-1]
 assert r['high_count_duration_s']==r['joint_duration_s']==0.
 assert r['traffic_state']=='no_observed_vehicles'
 assert 'free' not in r['traffic_state'] and 'not necessarily empty' in r['reason']
check('no_observation_resets_clocks_and_is_not_free_flow',no_observation)
check('count_at_P67_is_not_high_and_breaks_both_clocks',lambda: require(classify([W(0),W(10),W(20,count=2)])[-1]['high_count_duration_s']==classify([W(0),W(10),W(20,count=2)])[-1]['joint_duration_s']==0.))

motion_path=OUTPUTS/'paired_case_comparison_2026-09-28_v1/motion/Supplementary/NE12th/motion_10s.json'
ne12=json.loads(motion_path.read_text())
check('supplementary_NE12th_Train_short_source_identity',lambda:require(ne12['source']['split']=='Train' and ne12['source']['id']=='Bellevue_116th_NE12th__2017-09-11_07-08-32.mp4' and abs(ne12['source']['duration_s']-408.57685555555554)<1e-10))
RESULT={'passed':True,'check_count':len(CHECKS),'checks':CHECKS,'engine_path':str(ENGINE),'engine_sha256':ENGINE_SHA,'input_motion_path':str(motion_path),'input_motion_sha256':hashlib.sha256(motion_path.read_bytes()).hexdigest(),'test_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'scope':'frozen engine boundary behavior and input identity; production evidence_state overlay checked independently after outputs land','no_raw_frames_read':True,'synthetic_cases_are_software_tests_not_traffic_accuracy':True}
if __name__=='__main__':
 (OUT/'boundary_tests.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'passed':True,'check_count':len(CHECKS)}))
