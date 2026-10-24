"""Independent small-file verification; no frozen engine or production functions imported."""
from __future__ import annotations
import csv, hashlib, json, math
from collections import Counter, defaultdict
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parent;BASE=ROOT.parent
CASES=['Newport','NE12th','Eastgate','SE38th','NE8th']
READ={}
def read(p):
 b=p.read_bytes();READ[str(p)]={'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)};return json.loads(b)
def eq(a,b):
 if a is None or b is None:assert a is b,(a,b)
 elif isinstance(a,(float,int)) and not isinstance(a,bool):assert abs(a-b)<1e-8,(a,b)
 else:assert a==b,(a,b)
def quantile(values,q):
 v=sorted(values)
 if not v:return None
 rank=(len(v)-1)*q;lo=math.floor(rank);hi=math.ceil(rank)
 return v[lo]*(hi-rank)+v[hi]*(rank-lo) if hi!=lo else v[lo]

def verify():
 all_summaries=[];all_events=[];casechecks=[];nrows=0;invalid_motion_rows=0;unknown_count_only=0;numeric_without_P25=0
 expected_thresholds={}
 for case in CASES:
  p=ROOT/case/'states_10s.json';d=read(p);ip=BASE/f'paired_case_comparison_2026-09-28_v1/motion/Supplementary/{case}/motion_10s.json';m=read(ip)
  eq(d['input_motion_path'],str(ip));eq(d['input_motion_sha256'],READ[str(ip)]['sha256']);eq(d['source'],m['source']);eq(d['zones'],m['zones']);eq(d['held_intervals'],m['held_intervals'])
  source=d['source'];eq(source['split'],'Train' if case=='NE12th' else 'Validation')
  assert d['offline_relative_calibration'] is True and d['transfer_experiment'] is False
  assert len(d['windows'])==len(m['windows']);zones=[z['id'] for z in m['zones']]
  thresholds={}
  for z in zones:
   eligible=[(w,next(r for r in w['zones'] if r['zone_id']==z)) for w in m['windows'] if w['complete']]
   mot=[(w,r) for w,r in eligible if r['motion_available']]
   low=quantile([r['count_mean'] for w,r in eligible],.33);high=quantile([r['count_mean'] for w,r in eligible],.67);p25=quantile([r['motion_median_px_s'] for w,r in mot],.25) if len(mot)>=30 else None
   ref={'count_p33':low,'count_p67':high,'motion_low_upper':p25,'count_thresholds_distinguishable':high>low,'count_calibration_windows':len(eligible),'motion_calibration_windows':len(mot),'count_calibration_window_indices':[w['index'] for w,r in eligible],'motion_calibration_window_indices':[w['index'] for w,r in mot]}
   for k,v in ref.items():eq(d['thresholds'][z][k],v)
   for k,src in [('threshold_source_id','id'),('threshold_source_split','split'),('threshold_source_sha256','sha256')]:eq(d['thresholds'][z][k],source[src])
   assert d['thresholds'][z]['calibration']=='same_source_offline_not_transfer';thresholds[z]=ref
  expected_thresholds[case]={'source_id':source['id'],'split':source['split'],'input_sha256':READ[str(ip)]['sha256'],'thresholds':thresholds}
  count_start={z:None for z in zones};joint_start={z:None for z in zones};last_end=None
  flat=[];perzone={z:[] for z in zones}
  for w,mw in zip(d['windows'],m['windows']):
   for k,v in mw.items():
    if k!='zones':eq(w[k],v)
   eq(w['confirmation_source_time_s'],w['end_s'])
   contiguous=last_end is not None and abs(last_end-w['start_s'])<1e-8
   for r,mr in zip(w['zones'],mw['zones']):
    nrows+=1;z=r['zone_id'];t=thresholds[z]
    for k,v in mr.items():eq(r[k],v)
    if not contiguous:count_start[z]=None;joint_start[z]=None
    if not w['complete']:level='unknown'
    elif r['count_mean']==0:level='empty'
    elif not t['count_thresholds_distinguishable']:level='unknown'
    elif r['count_mean']<=t['count_p33']:level='low'
    elif r['count_mean']<=t['count_p67']:level='medium'
    else:level='high'
    available=r['motion_available'];lowmotion=(r['motion_median_px_s']<=t['motion_low_upper']) if available and t['motion_low_upper'] is not None else None
    if level=='high':
     if count_start[z] is None:count_start[z]=w['start_s']
    else:count_start[z]=None
    if level=='high' and lowmotion is True:
     if joint_start[z] is None:joint_start[z]=w['start_s']
    else:joint_start[z]=None
    highduration=w['end_s']-count_start[z] if count_start[z] is not None else 0.
    jointduration=w['end_s']-joint_start[z] if joint_start[z] is not None else 0.
    highflag=highduration>=30;jointflag=jointduration>=30
    if not w['complete']:hint='insufficient_window'
    elif level=='empty':hint='no_observed_vehicles'
    elif jointflag:hint='high_count_low_motion_persistent'
    elif highflag:hint='high_count_persistent'
    elif level=='unknown':hint='calibration_insufficient'
    elif lowmotion is None:hint='motion_insufficient'
    else:hint='no_persistent_signal'
    reasons=[]
    if not w['complete']:
     if w['held_duration_s']>0:reasons.append('held_image')
     if w['end_ticks']-w['start_ticks']<900000:reasons.append('incomplete_tail')
     if r['missing_duration_s']>0:reasons.append('missing_interval')
     if not reasons:reasons.append('incomplete_window')
    if not t['count_thresholds_distinguishable']:reasons.append('count_calibration_degenerate')
    if not available:reasons.append('motion_observation_insufficient')
    if t['motion_low_upper'] is None:reasons.append('motion_calibration_insufficient')
    display='unknown' if reasons else 'joint_hint' if jointflag else 'high_count_hint' if highflag else 'no_persistent_hint'
    expected={'relative_count_level':level,'state_hint':hint,'motion_low_relative':lowmotion,'high_count_duration_s':highduration,'joint_duration_s':jointduration,'high_count_persistent_count_only':highflag,'joint_hint':jointflag,'evidence_state':display,'evidence_unknown_reasons':reasons,'high_count_first_confirmed_here':highduration==30,'joint_first_confirmed_here':jointduration==30}
    for k,v in expected.items():eq(r[k],v)
    assert not jointflag or highflag
    if not available:assert r['motion_median_px_s'] is None and r['motion_low_relative'] is None;invalid_motion_rows+=1
    if available and t['motion_low_upper'] is None:assert r['motion_median_px_s'] is not None and r['motion_low_relative'] is None and display=='unknown';numeric_without_P25+=1
    unknown_count_only+=highflag and display=='unknown'
    if case=='NE8th':assert display=='unknown'
    if case=='Eastgate' and z=='road_left':assert display=='unknown'
    flat.append({'case':case,'source_id':source['id'],'split':source['split'],**{k:v for k,v in w.items() if k!='zones'},**r});perzone[z].append((w,r))
   last_end=w['end_s']
  eq(last_end,source['duration_s'])
  csvrows=list(csv.DictReader((ROOT/case/'states_10s.csv').open()));assert len(csvrows)==len(flat)
  for a,b in zip(csvrows,flat):
   for k,v in b.items():eq(a[k],json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else '' if v is None else str(v))
  events=[]
  for z in zones:
   for kind,field in [('high_count','high_count_persistent_count_only'),('joint','joint_hint')]:
    selected=[(w,r) for w,r in perzone[z] if r[field]];groups=[]
    for w,r in selected:
     if not groups or w['index']!=groups[-1][-1][0]['index']+1:groups.append([])
     groups[-1].append((w,r))
    for group in groups:
     first=group[0][0];last=group[-1][0]
     events.append({'case':case,'source_id':source['id'],'split':source['split'],'zone_id':z,'hint_type':kind,'condition_start_s':first['end_s']-30,'first_confirmation_s':first['end_s'],'last_confirmation_s':last['end_s'],'first_confirmed_window_index':first['index'],'last_confirmed_window_index':last['index'],'confirmed_windows':len(group),'unknown_evidence_confirmed_windows':sum(r['evidence_state']=='unknown' for w,r in group)})
  eq(read(ROOT/case/'hint_events.json'),events);all_events+=events
  summaries=read(ROOT/case/'summary.json');assert len(summaries)==len(zones)
  for s in summaries:
   z=s['zone_id'];pairs=perzone[z];t=thresholds[z];states=Counter(r['evidence_state'] for w,r in pairs);seconds=defaultdict(float);reasonsec=defaultdict(float)
   for w,r in pairs:
    dur=w['end_s']-w['start_s'];seconds[r['evidence_state']]+=dur
    for reason in r['evidence_unknown_reasons']:reasonsec[reason]+=dur
   ref={'case':case,'source_id':source['id'],'split':source['split'],'duration_s':source['duration_s'],'zone_id':z,'count_p33':t['count_p33'],'count_p67':t['count_p67'],'count_thresholds_distinguishable':t['count_thresholds_distinguishable'],'motion_p25_px_s':t['motion_low_upper'],'count_calibration_windows':t['count_calibration_windows'],'motion_calibration_windows':t['motion_calibration_windows'],'all_windows':len(pairs),'temporal_eligible_windows':sum(w['complete'] for w,r in pairs),'motion_available_windows':sum(r['motion_available'] for w,r in pairs),'high_count_hint_windows':sum(r['high_count_persistent_count_only'] for w,r in pairs),'joint_hint_windows':sum(r['joint_hint'] for w,r in pairs),'high_count_hint_events':sum(e['zone_id']==z and e['hint_type']=='high_count' for e in events),'joint_hint_events':sum(e['zone_id']==z and e['hint_type']=='joint' for e in events),'high_count_hints_with_unknown_evidence':sum(r['high_count_persistent_count_only'] and r['evidence_state']=='unknown' for w,r in pairs),'evidence_unknown_windows':states['unknown'],'evidence_unknown_s':seconds['unknown'],'evidence_unknown_fraction':seconds['unknown']/source['duration_s'],'evidence_state_windows':dict(states),'evidence_state_seconds':dict(seconds),'unknown_reason_seconds_nonexclusive':dict(reasonsec),'raw_count_level_windows':dict(Counter(r['relative_count_level'] for w,r in pairs)),'frozen_rule_hint_windows':dict(Counter(r['state_hint'] for w,r in pairs))}
   for k,v in ref.items():eq(s[k],v)
   eq(sum(seconds.values()),source['duration_s'])
  all_summaries+=summaries
  examples=read(ROOT/case/'examples.json');eq(examples['source'],source)
  for label,sample in examples['examples'].items():
   predicates={'joint_hint':lambda w,r:r['joint_first_confirmed_here'],'high_count_only_unknown':lambda w,r:r['high_count_persistent_count_only'] and r['evidence_state']=='unknown','unknown_evidence_with_positive_count':lambda w,r:w['complete'] and r['count_mean']>0 and r['evidence_state']=='unknown','held_window':lambda w,r:w['held_duration_s']>0,'incomplete_tail':lambda w,r:not w['full_valid_window'],'no_observations_unknown':lambda w,r:r['count_mean']==0 and w['complete']}
   selected=[(w,r) for w in d['windows'] for r in w['zones'] if predicates[label](w,r)]
   if not selected:assert sample is None;continue
   w,r=selected[0];eq(sample['window_index'],w['index']);eq(sample['zone_id'],r['zone_id']);eq(sample['source_id'],source['id']);eq(sample['split'],source['split'])
   for k,v in r.items():eq(sample[k],v)
  casechecks.append({'case':case,'split':source['split'],'windows':len(d['windows']),'region_windows':len(flat),'thresholds_independently_recomputed':True,'all_original_motion_and_count_fields_unchanged':True,'all_frozen_states_and_persistence_independently_recomputed':True,'all_evidence_gates_and_reasons_recomputed':True,'all_summaries_events_CSV_examples_verified':True,'passed':True})
 eq(read(ROOT/'region_summary.json'),all_summaries);eq(read(ROOT/'hint_events.json'),all_events)
 for filename,rows in [('region_summary.csv',all_summaries),('hint_events.csv',all_events)]:
  cr=list(csv.DictReader((ROOT/filename).open()));assert len(cr)==len(rows)
  for a,b in zip(cr,rows):
   for k,v in b.items():eq(a[k],json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else '' if v is None else str(v))
 index=read(ROOT/'results_index.json');assert index['completed'];assert index['relative_levels_not_comparable_across_sources'];assert not index['transfer_experiment_performed']
 for ix in index['results']:
  d=read(ROOT/ix['case']/'states_10s.json');s=[x for x in all_summaries if x['case']==ix['case']]
  ref={'source_id':d['source']['id'],'split':d['source']['split'],'duration_s':d['source']['duration_s'],'frames':d['source']['frame_count'],'regions':len(d['zones']),'windows':len(d['windows']),'complete_unheld_windows':sum(w['complete'] for w in d['windows']),'held_frames':len(d['held_intervals']),'held_s':sum(h['duration_s'] for h in d['held_intervals']),'held_affected_windows':sum(w['held_duration_s']>0 for w in d['windows']),'count_calibrated_regions':sum(t['count_thresholds_distinguishable'] for t in d['thresholds'].values()),'motion_calibrated_regions':sum(t['motion_low_upper'] is not None for t in d['thresholds'].values()),'high_count_hint_region_windows':sum(x['high_count_hint_windows'] for x in s),'joint_hint_region_windows':sum(x['joint_hint_windows'] for x in s),'unknown_evidence_region_windows':sum(x['evidence_unknown_windows'] for x in s),'all_region_windows':sum(x['all_windows'] for x in s)}
  for k,v in ref.items():eq(ix[k],v)
 assert nrows==6342 and len(all_summaries)==22
 result={'passed':True,'case_checks':casechecks,'region_windows':nrows,'regions':len(all_summaries),'events':len(all_events),'missing_motion_rows_kept_null':invalid_motion_rows,'absolute_motion_available_but_P25_missing_rows_kept_numeric_with_relative_null':numeric_without_P25,'count_only_hint_windows_with_unknown_composite':unknown_count_only,'all_22_threshold_sets_independently_recomputed':True,'production_functions_not_imported':True,'no_full_frames_or_videos_read':True,'no_Main_Test_read':True,'no_accuracy_evaluation':True,'read_inputs':READ,'verification_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
 (OUT/'expected_thresholds.json').write_text(json.dumps(expected_thresholds,ensure_ascii=False,indent=2)+'\n')
 (OUT/'independent_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k not in ('read_inputs','case_checks')},ensure_ascii=False))
if __name__=='__main__':verify()
