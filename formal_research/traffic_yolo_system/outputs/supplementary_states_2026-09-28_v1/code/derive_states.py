"""Reuse verified 10 s observations; frozen source-local calibration and 30 s hints.

No full frame logs, videos, detector, tracker, remote service or Unity access.
Frozen rule hints and a conservative joint-evidence display are kept separately.
"""
from pathlib import Path
from datetime import datetime, timezone
import copy,csv,hashlib,json,time,sys
from collections import Counter,defaultdict
import frozen_engine as E
OUT=Path(__file__).resolve().parents[1]
BASE=OUT.parent
PAIRED=BASE/'paired_case_comparison_2026-09-28_v1'
OLD=BASE/'five_intersection_states_2026-09-28_v1'
SUP=BASE/'supplementary_five_2026-09-28_v3_repair'
CASES={'Newport':'Bellevue_150th_Newport','NE12th':'Bellevue_116th_NE12th','Eastgate':'Bellevue_150th_Eastgate','SE38th':'Bellevue_150th_SE38th','NE8th':'Bellevue_Bellevue_NE8th'}
VERSION='supplementary-source-relative-states-v1'
INPUTS={}

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p,expected=None,lines=False):
 p=Path(p);b=p.read_bytes();h=hashlib.sha256(b).hexdigest()
 if expected:assert h==expected,(str(p),'SHA mismatch')
 INPUTS[str(p)]={'sha256':h,'bytes':len(b),'mtime_ns':p.stat().st_mtime_ns}
 return [json.loads(x) for x in b.splitlines()] if lines else json.loads(b)
def put(p,obj):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def csvput(p,rows):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader()
  writer.writerows([{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()} for r in rows])

RULES={
 'version':VERSION,'threshold_scope':'same supplementary source and same ROI, offline descriptive calibration',
 'parameters':E.PARAMETERS,'long_hold_threshold_seconds':.5,
 'calibration_eligibility':'only complete 10 s windows with valid coverage and no >0.5 s held frame overlap',
 'count_calibration':'all eligible count means including zeros; P33/P67 linear interpolation (n-1)*q; equal thresholds cannot separate nonzero count levels',
 'motion_calibration':'P25 from eligible windows with available motion; at least 30 such windows per region',
 'frozen_rule_semantics':'unchanged count/joint conditions and 30 s persistence; lack of motion resets joint streak, not valid high-count-only streak',
 'conservative_display_semantics':'evidence_state unknown whenever window incomplete/held/missing, count thresholds indistinguishable, motion unavailable, or P25 unavailable; preserves separate pure-count hint',
 'zero_observations':'relative_count_level=empty is only zero observed count; evidence_state unknown, never free flow',
 'null_semantics':'unavailable absolute motion remains null; available absolute motion remains numeric even if P25 is null; motion_low_relative is null if absolute motion or P25 unavailable',
 'confirmation_time':'window end in original source PTS; thresholds use the whole source, so this is retrospective replay timing, not prospective real-time availability',
 'transfer_experiment_performed':False,'no_cross_source_relative_label_comparison':True,
 'evidence_state_values':['unknown','no_persistent_hint','high_count_hint','joint_hint'],
 'high_count_persistent_count_only':'boolean condition high-count duration >=30 s; includes joint hints as a subset; do not add counts',
 'frozen_state_hint':'state_hint is exact frozen classifier output and must not override evidence_state unknown in future UI',
 'accuracy_evaluated':False,
}

def add_display(windows,thresholds):
 for w in windows:
  w['confirmation_source_time_s']=w['end_s']
  for r in w['zones']:
   t=thresholds[r['zone_id']];reasons=[]
   if not w['complete']:
    if w['held_duration_s']>0:reasons.append('held_image')
    if w['end_ticks']-w['start_ticks']<900000:reasons.append('incomplete_tail')
    if r['missing_duration_s']>0:reasons.append('missing_interval')
    if not reasons:reasons.append('incomplete_window')
   if t['density_high_lower']<=t['density_low_upper']:reasons.append('count_calibration_degenerate')
   if not r['motion_available']:reasons.append('motion_observation_insufficient')
   if t['motion_low_upper']<0:reasons.append('motion_calibration_insufficient')
   r['relative_count_level']=r.pop('density_level');r['relative_count_label']=r.pop('density_label')
   r['state_hint']=r.pop('traffic_state');r['state_hint_label']=r.pop('traffic_state_label')
   r['frozen_rule_reason']=r.pop('reason')
   r['motion_low_relative']=r['motion_median_px_s']<=t['motion_low_upper'] if r['motion_available'] and t['motion_low_upper']>=0 else None
   r['high_count_persistent_count_only']=r['high_count_duration_s']>=30
   r['joint_hint']=r['joint_duration_s']>=30
   r['evidence_state']='unknown' if reasons else ('joint_hint' if r['joint_hint'] else ('high_count_hint' if r['high_count_persistent_count_only'] else 'no_persistent_hint'))
   r['evidence_unknown_reasons']=reasons
   r['high_count_first_confirmed_here']=abs(r['high_count_duration_s']-30)<1e-8
   r['joint_first_confirmed_here']=abs(r['joint_duration_s']-30)<1e-8
   # Never coerce missing motion to zero; inputs already preserve null.
   assert r['motion_available']==(r['motion_median_px_s'] is not None)
   if not w['complete']:assert r['evidence_state']=='unknown' and r['high_count_duration_s']==r['joint_duration_s']==0
   if r['evidence_state']!='unknown':assert r['motion_available'] and r['motion_low_relative'] is not None
 return windows

def events_for(case,source,windows):
 events=[]
 for zone in [r['zone_id'] for r in windows[0]['zones']]:
  for kind,flag,duration in [('high_count','high_count_persistent_count_only','high_count_duration_s'),('joint','joint_hint','joint_duration_s')]:
   current=None
   for w in windows:
    r=next(x for x in w['zones'] if x['zone_id']==zone)
    if r[flag]:
     if current is None:
      current=dict(case=case,source_id=source['id'],split=source['split'],zone_id=zone,hint_type=kind,condition_start_s=w['end_s']-r[duration],first_confirmation_s=w['end_s'],last_confirmation_s=w['end_s'],first_confirmed_window_index=w['index'],last_confirmed_window_index=w['index'],confirmed_windows=0,unknown_evidence_confirmed_windows=0)
     current['last_confirmation_s']=w['end_s'];current['last_confirmed_window_index']=w['index'];current['confirmed_windows']+=1
     current['unknown_evidence_confirmed_windows']+=r['evidence_state']=='unknown'
    elif current is not None:events.append(current);current=None
   if current is not None:events.append(current)
 return events

def summaries(case,d,events):
 result=[]
 for z,t in d['thresholds'].items():
  rows=[(w,next(r for r in w['zones'] if r['zone_id']==z)) for w in d['windows']]
  state_counts=Counter(r['evidence_state'] for w,r in rows)
  state_seconds=defaultdict(float)
  for w,r in rows:state_seconds[r['evidence_state']]+=w['end_s']-w['start_s']
  reason_seconds=defaultdict(float)
  for w,r in rows:
   for reason in r['evidence_unknown_reasons']:reason_seconds[reason]+=w['end_s']-w['start_s']
  result.append(dict(case=case,source_id=d['source']['id'],split=d['source']['split'],duration_s=d['source']['duration_s'],zone_id=z,
   count_p33=t['count_p33'],count_p67=t['count_p67'],count_thresholds_distinguishable=t['count_thresholds_distinguishable'],motion_p25_px_s=t['motion_low_upper'],count_calibration_windows=t['count_calibration_windows'],motion_calibration_windows=t['motion_calibration_windows'],
   all_windows=len(rows),temporal_eligible_windows=sum(w['complete'] for w,r in rows),motion_available_windows=sum(r['motion_available'] for w,r in rows),
   high_count_hint_windows=sum(r['high_count_persistent_count_only'] for w,r in rows),joint_hint_windows=sum(r['joint_hint'] for w,r in rows),
   high_count_hint_events=sum(e['zone_id']==z and e['hint_type']=='high_count' for e in events),joint_hint_events=sum(e['zone_id']==z and e['hint_type']=='joint' for e in events),
   high_count_hints_with_unknown_evidence=sum(r['high_count_persistent_count_only'] and r['evidence_state']=='unknown' for w,r in rows),
   evidence_unknown_windows=state_counts['unknown'],evidence_unknown_s=state_seconds['unknown'],evidence_unknown_fraction=state_seconds['unknown']/d['source']['duration_s'],
   evidence_state_windows=dict(state_counts),evidence_state_seconds=dict(state_seconds),unknown_reason_seconds_nonexclusive=dict(reason_seconds),
   raw_count_level_windows=dict(Counter(r['relative_count_level'] for w,r in rows)),frozen_rule_hint_windows=dict(Counter(r['state_hint'] for w,r in rows))))
 return result

def select_examples(src,windows):
 sample={}
 for label,predicate in [('joint_hint',lambda w,r:r['joint_first_confirmed_here']),('high_count_only_unknown',lambda w,r:r['high_count_persistent_count_only'] and r['evidence_state']=='unknown'),('unknown_evidence_with_positive_count',lambda w,r:w['complete'] and r['count_mean']>0 and r['evidence_state']=='unknown'),('held_window',lambda w,r:w['held_duration_s']>0),('incomplete_tail',lambda w,r:not w['full_valid_window']),('no_observations_unknown',lambda w,r:r['count_mean']==0 and w['complete'])]:
  choices=[dict(source_id=src['id'],split=src['split'],window_index=w['index'],start_s=w['start_s'],end_s=w['end_s'],confirmation_source_time_s=w['end_s'],**r) for w in windows for r in w['zones'] if predicate(w,r)]
  sample[label]=choices[0] if choices else None
 return {'selection':'earliest qualifying region-window in source order; illustrative, not representative samples; joint example selects first confirmation, count-only/unknown may occur later in a continuing run','source':src,'examples':sample}

def main():
 start=time.perf_counter()
 if (OUT/'derivation_completion.json').exists():raise SystemExit('Completed output exists: use a new independent version directory.')
 old_final=read(OLD/'final_state_verification.json');assert old_final['completed']
 frozen_sha=sha(OUT/'code/frozen_engine.py');assert frozen_sha==old_final['files']['code/engine.py']
 # Required result and checksum records; no full media/frames reread.
 delivered=read(SUP/'final_delivery_verification.json');index=read(SUP/'supplementary_results_index.json');assert delivered['completed'] and index['completed']
 paired_final=read(PAIRED/'final_verification.json');assert paired_final['completed']
 mv=read(PAIRED/'motion/verification.json',paired_final['output_files']['motion/verification.json']['sha256']);assert mv['completed']
 independent=read(PAIRED/'motion/independent_verification.json',paired_final['output_files']['motion/independent_verification.json']['sha256']);assert independent['passed']
 read(PAIRED/'motion/input_manifest.json',mv['outputs_sha256']['input_manifest.json'])
 read(PAIRED/'audit/comparability_audit.json',paired_final['output_files']['audit/comparability_audit.json']['sha256'])
 for p in [SUP/'补充批次完整结果与说明.md',OLD/'五路口交通状态分析总报告.md',PAIRED/'五路口原案例与补充时段独立对比总报告.md']:
  b=p.read_bytes();INPUTS[str(p)]={'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b),'mtime_ns':p.stat().st_mtime_ns}
 totals=[];all_events=[];lineage=[];case_summaries=[]
 for case,camera in CASES.items():
  mpath=PAIRED/f'motion/Supplementary/{case}/motion_10s.json'
  m=read(mpath,mv['outputs_sha256'][f'Supplementary/{case}/motion_10s.json'])
  assert sha(mpath)==paired_final['output_files'][f'motion/Supplementary/{case}/motion_10s.json']['sha256']
  ix=next(x for x in index['results'] if x['camera']==camera);src=m['source']
  assert ix['source_id']==src['id'] and ix['split']==src['split']==('Train' if case=='NE12th' else 'Validation')
  assert ix['frames']==src['frame_count'] and abs(ix['duration_seconds']-src['duration_s'])<1e-8
  attempt=SUP/'delivery'/camera/'full_data_archive/attempt-000001'
  completion=read(attempt/'completion.json');qc=read(Path(ix['qc']));assert qc['status']=='passed'
  roi=read(SUP/'config'/camera/'roi.json',m['roi_sha256'])
  assert completion['fingerprint']==m['configuration_sha256'] and qc['model_sha256']==m['model_sha256']
  assert qc['source_sha256']==src['sha256'] and qc['source_id']==src['id']
  auth=read(attempt/'windows.jsonl',completion['files']['windows.jsonl']['sha256'],lines=True)
  by={(r['window_index'],r['zone_name']):r for r in auth}
  windows=copy.deepcopy(m['windows']);zones=[r['id'] for r in m['zones']]
  for w in windows:
   assert w['start_ticks']==w['index']*900000 and w['end_ticks']==round(w['end_s']*90000)
   assert w['complete']==(w['full_valid_window'] and w['held_duration_s']==0)
   for r in w['zones']:
    a=by[w['index'],r['zone_id']]
    assert a['source_id']==src['id'] and a['split']==src['split']
    assert a['mean_current_vehicles']==r['count_mean']
    assert abs(a['valid_duration_seconds']-r['valid_duration_s'])<1e-8
    assert (r['motion_median_px_s'] is not None)==r['motion_available']
  # Only these two frozen functions are invoked. No aggregation or inference.
  th=E.calibrate(windows,zones);E.classify_windows(windows,th);add_display(windows,th)
  thresholds={}
  for z,t in th.items():
   thresholds[z]=dict(count_p33=t['density_low_upper'],count_p67=t['density_high_lower'],motion_low_upper=t['motion_low_upper'] if t['motion_low_upper']>=0 else None,
    count_thresholds_distinguishable=t['density_high_lower']>t['density_low_upper'],count_calibration_windows=t['density_calibration_windows'],motion_calibration_windows=t['motion_calibration_windows'],
    count_calibration_window_indices=[w['index'] for w in m['windows'] if w['complete']],motion_calibration_window_indices=[w['index'] for w in m['windows'] if w['complete'] and next(r for r in w['zones'] if r['zone_id']==z)['motion_available']],
    threshold_source_id=src['id'],threshold_source_split=src['split'],threshold_source_sha256=src['sha256'],calibration='same_source_offline_not_transfer')
  d=dict(schema_version=VERSION,case=case,source=src,model_sha256=m['model_sha256'],roi_sha256=m['roi_sha256'],configuration_sha256=m['configuration_sha256'],zones=m['zones'],thresholds=thresholds,windows=windows,held_intervals=m['held_intervals'],
   rules=RULES,input_motion_path=str(mpath),input_motion_sha256=sha(mpath),frozen_engine_sha256=frozen_sha,offline_relative_calibration=True,transfer_experiment=False,
   lighting_caution={'NE8th':'Night source: low or zero observed counts do not establish an empty or freely flowing road.','Eastgate':'Dawn-to-daylight change can affect detectability and the same-source count baseline.','SE38th':'Dusk-to-night change can affect detectability and the same-source count baseline.','NE12th':'Short daylight Train source, not independent Validation.','Newport':'Evening illumination and source-specific filter applicability limit interpretation.'}[case],
   lineage={'native_frames_sha256_from_completed_receipt':completion['files']['frames.jsonl']['sha256'],'native_windows_sha256':completion['files']['windows.jsonl']['sha256'],'full_frames_not_reread_this_stage':True,'prior_motion_verification':str(PAIRED/'motion/verification.json')})
  events=events_for(case,src,windows);summary=summaries(case,d,events);totals+=summary;all_events+=events
  dest=OUT/case;put(dest/'states_10s.json',d);put(dest/'rules_and_thresholds.json',{'rules':RULES,'thresholds':thresholds,'source':src,'frozen_engine_sha256':frozen_sha})
  flat=[dict(case=case,source_id=src['id'],split=src['split'],**{k:v for k,v in w.items() if k!='zones'},**r) for w in windows for r in w['zones']]
  csvput(dest/'states_10s.csv',flat);put(dest/'summary.json',summary);put(dest/'hint_events.json',events)
  put(dest/'examples.json',select_examples(src,windows))
  cs=dict(case=case,source_id=src['id'],split=src['split'],duration_s=src['duration_s'],frames=src['frame_count'],regions=len(zones),windows=len(windows),complete_unheld_windows=sum(w['complete'] for w in windows),held_frames=len(m['held_intervals']),held_s=sum(h['duration_s'] for h in m['held_intervals']),held_affected_windows=sum(w['held_duration_s']>0 for w in windows),count_calibrated_regions=sum(t['count_thresholds_distinguishable'] for t in thresholds.values()),motion_calibrated_regions=sum(t['motion_low_upper'] is not None for t in thresholds.values()),high_count_hint_region_windows=sum(r['high_count_hint_windows'] for r in summary),joint_hint_region_windows=sum(r['joint_hint_windows'] for r in summary),unknown_evidence_region_windows=sum(r['evidence_unknown_windows'] for r in summary),all_region_windows=len(flat),states_path=str(dest/'states_10s.json'))
  case_summaries.append(cs);print(json.dumps(cs),flush=True)
 csvput(OUT/'region_summary.csv',totals);put(OUT/'region_summary.json',totals);put(OUT/'hint_events.json',all_events)
 if all_events:csvput(OUT/'hint_events.csv',all_events)
 put(OUT/'results_index.json',{'version':VERSION,'completed':True,'results':case_summaries,'relative_levels_not_comparable_across_sources':True,'transfer_experiment_performed':False})
 put(OUT/'method.json',RULES);put(OUT/'input_manifest.json',INPUTS)
 for path,s in INPUTS.items():assert(Path(path).stat().st_size,Path(path).stat().st_mtime_ns)==(s['bytes'],s['mtime_ns'])
 put(OUT/'derivation_completion.json',{'completed':True,'created_at':datetime.now(timezone.utc).isoformat(),'version':VERSION,'elapsed_s':time.perf_counter()-start,'sources':5,'region_windows':sum(x['all_region_windows'] for x in case_summaries),'inputs_unchanged':True,'cached_count_motion_reused':True,'full_frame_logs_reread':False,'video_read':False,'new_inference':False,'main_test_images_labels_read':False,'unity_modified':False,'numerical_method_unchanged':True,'conservative_evidence_display_added':True,'transfer_experiment_performed':False,'script_sha256':sha(Path(__file__)),'frozen_engine_sha256':frozen_sha})

if __name__=='__main__':main()
