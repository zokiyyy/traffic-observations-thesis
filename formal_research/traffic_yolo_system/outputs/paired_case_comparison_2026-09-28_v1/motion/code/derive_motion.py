"""Read-only motion extraction, no calibration, state classification, detector or media I/O."""
from __future__ import annotations
import csv, hashlib, json, math, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import engine as E

OUT = Path(__file__).resolve().parents[1]
OUTPUTS = OUT.parents[1]
OLD = OUTPUTS / 'five_intersection_states_2026-09-28_v1'
SUP = OUTPUTS / 'supplementary_five_2026-09-28_v3_repair'
VERSION = 'paired-absolute-image-motion-v1'
CAMERAS = {
 'Newport': ('Bellevue_150th_Newport', '2017-09-10_18-08-24', 'Validation'),
 'NE12th': ('Bellevue_116th_NE12th', '2017-09-11_07-08-32', 'Train'),
 'Eastgate': ('Bellevue_150th_Eastgate', '2017-09-11_06-08-30', 'Validation'),
 'SE38th': ('Bellevue_150th_SE38th', '2017-09-10_19-08-25', 'Validation'),
 'NE8th': ('Bellevue_Bellevue_NE8th', '2017-09-11_02-08-29', 'Validation'),
}
INPUTS = {}
METHOD = {
 'version': VERSION, 'engine_origin': str(OLD / 'code/engine.py'),
 'motion_unit': 'source-image pixels per second, not km/h',
 'history': 'same ID and same assigned zone, consecutive observed frames; bottom-center displacement over 1.0 to 1.2 s original PTS',
 'window_statistic': 'Within each 10 s window, each ID rate is averaged using its valid motion durations; these ID averages are combined by a median weighted by valid motion durations.',
 'history_reset': 'missing observation, zone change, invalid frame, nonconsecutive frame index, and before/after any presentation duration >0.5 s',
 'availability': 'complete valid 10 s window, no overlap with presentation duration >0.5 s, motion vehicle-seconds / observed vehicle-seconds >=0.5, motion vehicle-seconds >=5',
 'unknown': 'null, never filled with zero',
 'summary_statistic': 'Duration-weighted P25/P50/P75 of available 10 s motion medians; lower inverse-CDF weighted quantiles. All available windows are 10 s.',
 'calibration': 'none; no P25 low-motion threshold, count-level or 30 s traffic state is newly calculated',
 'caution': 'Compare only same ROI after reviewing acquisition/configuration comparability. Image motion is perspective-dependent; evidence selection, box jitter, occlusion and tracking fragmentation affect these values. No congestion, flow or accuracy claims.',
}

def put(path, obj):
 path.parent.mkdir(parents=True, exist_ok=True)
 path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)+'\n')

def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
 return h.hexdigest()

def track(path, sha):
 st=path.stat(); INPUTS[str(path)]={'sha256':sha,'bytes':st.st_size,'mtime_ns':st.st_mtime_ns}

def load(path, expected=None, lines=False):
 data=path.read_bytes(); sha=hashlib.sha256(data).hexdigest()
 if expected: assert sha==expected, ('input hash',str(path))
 track(path,sha)
 return [json.loads(x) for x in data.splitlines()] if lines else json.loads(data)

def write_csv(path, rows):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def strip_windows(windows):
 result=[]
 for w in windows:
  x={k:w[k] for k in ('index','start_s','end_s','complete','full_valid_window','held_duration_s')}
  x['start_ticks']=round(x['start_s']*90000); x['end_ticks']=round(x['end_s']*90000)
  x['quality']='held_image' if x['held_duration_s'] else ('complete' if x['complete'] else 'incomplete_or_missing')
  x['zones']=[]
  for r in w['zones']:
   y={k:r[k] for k in ('zone_id','count_mean','motion_median_px_s','motion_available','motion_coverage','motion_vehicle_seconds','observed_vehicle_seconds','motion_track_count','valid_duration_s','missing_duration_s')}
   if y['motion_median_px_s'] is not None and y['motion_median_px_s'] <0: y['motion_median_px_s']=None
   if y['count_mean'] is not None and y['count_mean']<0: y['count_mean']=None
   reasons=[]
   if x['held_duration_s']>0: reasons.append('long_held_frame_overlap')
   if y['missing_duration_s']>0: reasons.append('missing_interval')
   if x['end_ticks']-x['start_ticks']<900000: reasons.append('short_tail')
   if y['observed_vehicle_seconds']==0: reasons.append('no_current_observations')
   if y['motion_coverage']<0.5: reasons.append('coverage_below_50_percent')
   if y['motion_vehicle_seconds']<5: reasons.append('less_than_5_motion_vehicle_seconds')
   y['unavailability_reasons']=reasons
   expected=x['complete'] and y['motion_coverage']>=0.5 and y['motion_vehicle_seconds']>=5
   assert y['motion_available']==expected==(y['motion_median_px_s'] is not None)
   assert y['motion_available'] == (not reasons)
   assert -1e-12<=y['motion_coverage']<=1+1e-12
   assert y['motion_vehicle_seconds']<=y['observed_vehicle_seconds']+1e-8
   if y['valid_duration_s']:
    assert abs(y['count_mean']*y['valid_duration_s']-y['observed_vehicle_seconds'])<1e-6
   assert abs(y['valid_duration_s']+y['missing_duration_s']-(x['end_s']-x['start_s']))<1e-7
   x['zones'].append(y)
  result.append(x)
 return result

def audited_stream(frames,timing,source,split,comp,roi_sha,pts,summary):
 h,th=hashlib.sha256(),hashlib.sha256(); cursor=0; count=0
 before=[(p.stat().st_size,p.stat().st_mtime_ns) for p in (frames,timing)]
 with frames.open('rb') as f,timing.open('rb') as t:
  for line in f:
   h.update(line); r=json.loads(line); tl=t.readline(); assert tl,'truncated PTS'; th.update(tl); q=json.loads(tl)
   assert r['source_id']==source and r['split']==split and r['synthetic'] is False
   assert r['configuration_sha256']==comp['fingerprint'] and r['roi_sha256']==roi_sha
   assert (r['time_base_num'],r['time_base_den'])==(1,90000)
   assert r['frame_index']==q['frame_index']==count and r['pts']==q['pts']==cursor
   assert (q['width'],q['height'])==(1280,720)
   assert r['duration_ticks']>0 and abs(r['duration_ticks']/90000-q['duration_seconds'])<1e-8
   for k in ('timestamp_seconds','source_timestamp_seconds'): assert abs(r[k]-cursor/90000)<1e-8
   assert abs(r['duration_seconds']-r['duration_ticks']/90000)<1e-8
   cursor+=r['duration_ticks'];count+=1
   yield r
  assert not t.readline(),'extra PTS rows'
 assert h.hexdigest()==comp['files']['frames.jsonl']['sha256']
 assert th.hexdigest()==pts['timing_frames_sha256']
 assert count==summary['frame_count'] and abs(cursor/90000-summary['duration_seconds'])<1e-7
 assert before==[(p.stat().st_size,p.stat().st_mtime_ns) for p in (frames,timing)]
 track(frames,h.hexdigest());track(timing,th.hexdigest())

def supplementary(name):
 camera,stamp,split=CAMERAS[name];source=camera+'__'+stamp+'.mp4'
 delivery=SUP/'delivery'/camera; attempt=delivery/'full_data_archive/attempt-000001'; native=delivery/'full_data_archive/output'
 local=load(delivery/'local_completion.json'); server=load(delivery/'server_camera_completion.json',local['server_receipt_sha256'])
 comp=load(attempt/'completion.json'); summary=load(attempt/'run_summary.json',comp['files']['run_summary.json']['sha256'])
 pts=load(native/'pts_summary.json'); roi_path=SUP/'config'/camera/'roi.json'; roi=load(roi_path);roi_sha=INPUTS[str(roi_path)]['sha256']
 qc=load(delivery/'summary_archive/output/qc_summary.json')
 assert local['completed'] and local['full_data_verified'] and server['status']=='completed' and qc['status']=='passed'
 assert local['source_id']==server['source_id']==comp['source_id']==summary['source_id']==roi['source_video']==source
 assert server['split']==summary['split']==roi['source_split']==split
 assert server['source_sha256']==roi['source_sha256']==qc['source_sha256']
 assert roi_sha==summary['roi_sha256'];assert summary['configuration_sha256']==comp['fingerprint']
 assert (roi['frame_width'],roi['frame_height'],roi['assignment_point'])==(1280,720,'bottom_center')
 zones=[z['zone_name'] for z in roi['zones']]
 auth=load(attempt/'windows.jsonl',comp['files']['windows.jsonl']['sha256'],lines=True)
 # Adapt only identity/split validation to the explicitly approved five-source allowlist.
 # All numerical operations remain the byte-identical frozen engine; no calibrate/classify calls.
 def validate(row):
  assert row.get('source_id',row.get('source'))==source and row.get('split')==split
  assert row['roi_sha256']==roi_sha and row['configuration_sha256']==comp['fingerprint'] and row['synthetic'] is False
 E.validate_source=validate
 agg=E.aggregate_frames(audited_stream(attempt/'frames.jsonl',native/'timing_frames.jsonl',source,split,comp,roi_sha,pts,summary),zones)
 gaps=load(native/'pts_gaps.jsonl',lines=True)
 expected={(g['frame_index']-1,g['pts']-g['pts_delta_ticks'],g['pts']) for g in gaps if g['pts_delta_ticks']>45000}
 actual={(h['frame_index'],h['start_ticks'],h['end_ticks']) for h in agg['held_intervals']}
 assert expected==actual,'held frame / PTS audit mismatch'
 windows=strip_windows(E.build_windows(auth,agg,zones))
 return {'version':VERSION,'case':name,'batch':'Supplementary','source':{'id':source,'split':split,'sha256':server['source_sha256'],'duration_s':summary['duration_seconds'],'frame_count':summary['frame_count'],'width':1280,'height':720,'time_base':{'num':1,'den':90000}},'roi_sha256':roi_sha,'configuration_sha256':comp['fingerprint'],'model_sha256':summary['model_sha256'],'zones':[{'id':z['zone_name'],'polygon_points':z['polygon_points']} for z in roi['zones']],'windows':windows,'held_intervals':agg['held_intervals'],'derivation':'frozen engine aggregate_frames and build_windows only','checks':{'passed':True,'original_frame_sha_verified':True,'PTS_rowwise_match':True,'source_split_checked_every_frame':True,'ROI_and_configuration_checked_every_frame':True,'original_10s_count_means_recomputed':True,'held_intervals_match_PTS_gaps':True,'missing_duration_s':(agg['duration_ticks']-sum(agg['valid_ticks'].values()))/90000}}

def original(name,seal,prior_manifest):
 path=OLD/name/'states_10s.json';d=load(path,seal['files'][name+'/states_10s.json'])
 assert d['validation']['passed'] and d['source']['split'].lower()=='validation'
 roi_path=Path(d['source_paths']['roi'])
 if str(roi_path) in prior_manifest:
  roi_sha=prior_manifest[str(roi_path)]['sha256']
 else:
  assert name=='Newport'
  receipt=load(OUTPUTS/'traffic_state_demo_newport_2026-09-27/generation_receipt.json')
  assert receipt['source_id']==d['source']['id'] and receipt['split']=='Validation' and receipt['status']=='completed'
  roi_sha=receipt['verified_hashes']['roi_sha256']
 roi=load(roi_path,roi_sha)
 assert roi['source_video']==d['source']['id'] and roi['source_split']=='Validation'
 source={k:d['source'][k] for k in ('id','duration_s','frame_count','width','height','time_base')}
 source_sha=d['source'].get('sha256',roi.get('source_sha256'))
 if source_sha is None:
  receipt=load(OUTPUTS/'traffic_state_demo_newport_2026-09-27/generation_receipt.json')
  assert receipt['source_id']==source['id'] and receipt['split']=='Validation' and receipt['status']=='completed'
  source_sha=receipt['source_video_sha256']
 source.update(split='Validation',sha256=source_sha)
 source['prior_split_spelling']=d['source']['split']
 windows=strip_windows(d['windows'])
 return {'version':VERSION,'case':name,'batch':'Original','source':source,'zones':[{'id':z['zone_name'],'polygon_points':z['polygon_points']} for z in roi['zones']],'windows':windows,'held_intervals':d['held_intervals'],'derivation':'absolute motion fields reused exactly from hash-verified original states_10s.json; no relative state fields exported','reused_file':str(path),'checks':{'passed':True,'sealed_state_hash_verified':True,'motion_values_reused_exactly':True,'original_prior_validation':d['validation'],'evidence_gates_independently_checked':True,'missing_duration_s':sum(w['zones'][0]['missing_duration_s'] for w in windows)}}

def weighted_q(values,q):
 if not values:return None
 target=sum(weight for value,weight in values)*q; cumulative=0
 for value,weight in sorted(values):
  cumulative+=weight
  if cumulative>=target:return value
 raise AssertionError('weighted quantile')

def summarize(d):
 result=[]; duration=d['source']['duration_s']; windows=d['windows']
 for zone in [z['id'] for z in d['zones']]:
  pairs=[(w,next(r for r in w['zones'] if r['zone_id']==zone)) for w in windows]
  available=[(w,r) for w,r in pairs if r['motion_available']]
  motion=[(r['motion_median_px_s'],w['end_s']-w['start_s']) for w,r in available]
  evidence_s=sum(weight for value,weight in motion)
  observed_windows=[(w,r) for w,r in pairs if r['observed_vehicle_seconds']>0]
  complete_observed=[(w,r) for w,r in observed_windows if w['complete']]
  exposure=sum(r['observed_vehicle_seconds'] for w,r in pairs)
  mexp=sum(r['motion_vehicle_seconds'] for w,r in pairs)
  row={'case':d['case'],'batch':d['batch'],'source_id':d['source']['id'],'split':d['source']['split'],'zone_id':zone,'source_duration_s':duration,'all_windows':len(windows),'complete_unheld_windows':sum(w['complete'] for w in windows),'available_motion_windows':len(available),'available_motion_duration_s':evidence_s,'available_motion_time_fraction':evidence_s/duration,'unknown_motion_time_fraction':1-evidence_s/duration,'complete_observed_windows':len(complete_observed),'available_among_complete_observed_windows':len(available)/len(complete_observed) if complete_observed else None,'observed_vehicle_seconds':exposure,'raw_continuous_motion_vehicle_seconds':mexp,'raw_motion_exposure_coverage':mexp/exposure if exposure else None,'motion_p25_px_s':weighted_q(motion,.25),'motion_p50_px_s':weighted_q(motion,.5),'motion_p75_px_s':weighted_q(motion,.75),'motion_min_px_s':min((v for v,w in motion),default=None),'motion_max_px_s':max((v for v,w in motion),default=None),'missing_duration_s':sum(r['missing_duration_s'] for w,r in pairs),'held_frame_count':len(d['held_intervals']),'held_duration_s':sum(h['duration_s'] for h in d['held_intervals']),'held_affected_windows':sum(w['held_duration_s']>0 for w in windows),'held_affected_window_duration_s':sum(w['end_s']-w['start_s'] for w in windows if w['held_duration_s']>0),'tail_duration_s':sum(w['end_s']-w['start_s'] for w in windows if w['end_ticks']-w['start_ticks']<900000),'no_observation_window_duration_s':sum(w['end_s']-w['start_s'] for w,r in pairs if r['observed_vehicle_seconds']==0)}
  result.append(row)
 return result

def export(d):
 dest=OUT/d['batch']/d['case']; put(dest/'motion_10s.json',d)
 rows=[]
 for w in d['windows']:
  for r in w['zones']:
   row={k:v for k,v in w.items() if k!='zones'};row.update(r);row['unavailability_reasons']=';'.join(r['unavailability_reasons']);rows.append(row)
 write_csv(dest/'motion_10s.csv',rows)
 put(dest/'verification.json',d['checks'])


def main():
 started=time.perf_counter()
 old_seal=load(OLD/'final_state_verification.json');sup_seal=load(SUP/'final_delivery_verification.json')
 prior_manifest=load(OLD/'input_manifest.json',old_seal['files']['input_manifest.json'])
 assert old_seal['completed'] and sup_seal['completed']
 original_engine=OLD/'code/engine.py';engine_sha=digest(original_engine);track(original_engine,engine_sha)
 assert digest(OUT/'code/engine.py')==engine_sha==old_seal['files']['code/engine.py']
 old_driver=OLD/'code/derive_states.py';track(old_driver,digest(old_driver))
 results=[]
 for name in CAMERAS:
  old=original(name,old_seal,prior_manifest);export(old);results.append(old)
  new=supplementary(name);export(new);results.append(new)
  assert old['zones']==new['zones'], ('ROI geometry differs',name)
  print(json.dumps({'case':name,'done':True,'original_s':old['source']['duration_s'],'supplementary_s':new['source']['duration_s'],'supplementary_split':new['source']['split'],'supplementary_held_frames':len(new['held_intervals'])}),flush=True)
 summary=[row for d in results for row in summarize(d)]
 put(OUT/'motion_summary.json',{'method':METHOD,'rows':summary});write_csv(OUT/'motion_summary.csv',summary);put(OUT/'method.json',METHOD)
 for path,metadata in INPUTS.items():
  p=Path(path);assert (p.stat().st_size,p.stat().st_mtime_ns)==(metadata['bytes'],metadata['mtime_ns']),('input changed',path)
 put(OUT/'input_manifest.json',INPUTS)
 put(OUT/'verification.json',{'completed':True,'version':VERSION,'at':datetime.now(timezone.utc).isoformat(),'elapsed_s':time.perf_counter()-started,'source_count':10,'zone_summary_rows':len(summary),'source_zone_window_rows':sum(sum(len(w['zones']) for w in d['windows']) for d in results),'all_input_stats_unchanged':True,'frozen_engine_copy_sha256':engine_sha,'source_bound_allowlist_used':True,'numerical_engine_unchanged':True,'original_state_hashes_verified':True,'supplementary_frame_PTS_hashes_verified':True,'supplementary_10s_count_means_recomputed':True,'same_ROI_polygon_and_order_each_pair':True,'motion_availability_gate_independently_checked':True,'Train_source_preserved':True,'no_relative_states_generated':True,'no_YOLO_or_tracker_rerun':True,'no_Main_Test_read':True,'no_original_artifact_modified':True,'accuracy_evaluated':False,'code_sha256':{p.name:digest(p) for p in (OUT/'code').glob('*.py')},'outputs_sha256':{str(p.relative_to(OUT)):digest(p) for p in OUT.rglob('*') if p.is_file() and p.suffix in ('.json','.csv') and p.name!='verification.json'}})

if __name__=='__main__':main()
