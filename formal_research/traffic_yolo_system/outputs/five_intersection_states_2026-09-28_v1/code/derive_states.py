"""Read-only completed Validation inputs; isolated offline derivation. No media or ML imports."""
import csv, json, hashlib, time, fcntl, sys, shutil, math
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta, timezone
import engine as E
OUT=Path(__file__).resolve().parents[1]
SYSTEM=OUT.parents[1]
R2=SYSTEM/'outputs/five_intersections_2026-09-27_r2'
NEWPORT=SYSTEM/'outputs/traffic_state_demo_newport_2026-09-27'
CAMERAS={'NE12th':'Bellevue_116th_NE12th','Eastgate':'Bellevue_150th_Eastgate','SE38th':'Bellevue_150th_SE38th','NE8th':'Bellevue_Bellevue_NE8th'}
VERSION='five-intersection-relative-state-v1'
INPUTS={}

def read(p):
 p=Path(p); b=p.read_bytes(); INPUTS[str(p)]={'sha256':hashlib.sha256(b).hexdigest(),'size':len(b),'mtime_ns':p.stat().st_mtime_ns}; return json.loads(b)
def put(p,j):
 p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(j,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(2**20),b''):h.update(b)
 return h.hexdigest()
def rows(p): return [json.loads(x) for x in Path(p).read_text().splitlines()]
def track_input(p,h):
 p=Path(p); INPUTS[str(p)]={'sha256':h,'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns}
def checked_rows(p,expected):
 b=Path(p).read_bytes(); h=hashlib.sha256(b).hexdigest(); assert h==expected,(str(p),'hash');track_input(p,h);return [json.loads(x) for x in b.splitlines()]

def audit_stream(frames,timing,source,fingerprint,roi_sha,expected_hash,expected_timing,summary):
 h,th=hashlib.sha256(),hashlib.sha256(); count=0;cursor=0
 before=[(p.stat().st_size,p.stat().st_mtime_ns) for p in (frames,timing)]
 with frames.open('rb') as f,timing.open('rb') as t:
  for line in f:
   h.update(line);r=json.loads(line);tl=t.readline(); assert tl,'timing truncated';th.update(tl);q=json.loads(tl)
   assert r['source_id']==source and r['split']=='Validation' and r['synthetic'] is False
   assert r['configuration_sha256']==fingerprint and r['roi_sha256']==roi_sha
   assert (r['time_base_num'],r['time_base_den'])==(1,90000)
   assert r['frame_index']==q['frame_index']==count and r['pts']==q['pts']==cursor
   assert r['duration_ticks']>0 and abs(r['duration_ticks']/90000-q['duration_seconds'])<1e-8
   for field in ['timestamp_seconds','source_timestamp_seconds']:assert abs(r[field]-cursor/90000)<1e-8
   assert abs(r['duration_seconds']-r['duration_ticks']/90000)<1e-8
   cursor+=r['duration_ticks'];count+=1
   yield r
  assert not t.readline(),'extra timing rows'
 assert h.hexdigest()==expected_hash and th.hexdigest()==expected_timing
 assert count==summary['frame_count'] and abs(cursor/90000-summary['duration_seconds'])<1e-7
 assert before==[(p.stat().st_size,p.stat().st_mtime_ns) for p in (frames,timing)]
 track_input(frames,h.hexdigest());track_input(timing,th.hexdigest())

def normalize(name,dataset):
 night=name=='NE12th'
 start=datetime.strptime(dataset['source']['id'].split('__')[1].replace('.mp4',''),'%Y-%m-%d_%H-%M-%S')
 for w in dataset['windows']:
  w['full_valid_window']=w.get('full_valid_window',w['complete'])
  w['held_duration_s']=w.get('held_duration_s',0.)
  w['quality']='held_image' if w['held_duration_s'] else ('complete' if w['complete'] else 'incomplete_or_missing')
  w['start_ticks']=round(w['start_s']*90000);w['end_ticks']=round(w['end_s']*90000)
  w['source_clock_start']=(start+timedelta(seconds=w['start_s'])).isoformat(timespec='milliseconds')
  w['source_clock_end']=(start+timedelta(seconds=w['end_s'])).isoformat(timespec='milliseconds')
  for r in w['zones']:
   r['relative_count_level']=r.pop('density_level');r['relative_count_label']=r.pop('density_label')
   r['state_hint']=r.pop('traffic_state');r['state_label']=r.pop('traffic_state_label')
   for k in ['count_mean','motion_median_px_s']:
    if r[k]<0:r[k]=None
   if w['held_duration_s']:
    r.update(state_hint='held_image_evidence_insufficient',state_label='Held image: insufficient evidence',reason='Original frame presentation interval exceeds 0.5 s. Entire intersecting window excluded from classification/calibration. Persistence reset.',motion_median_px_s=None,motion_available=False)
   if night:
    r['night_caution']='Night glare and missed detections possible; low/zero observations do not establish free flow.'
   if night and r['state_hint']=='no_observed_vehicles':
    r.update(state_hint='night_no_observations',state_label='Night: no vehicles detected; road condition unknown')
   r['motion_low_relative']=None if r['motion_median_px_s'] is None or dataset['thresholds'][r['zone_id']]['motion_low_upper']<0 else r['motion_median_px_s']<=dataset['thresholds'][r['zone_id']]['motion_low_upper']
 dataset.update(schema_version=VERSION,night_case=night,clock_note='Filename-derived source clock, timezone unverified; authoritative position is original relative PTS.',offline_window_summary=True,calibration_same_source=True)
 dataset['source']['time_base']={'num':1,'den':90000}
 for z,t in dataset['thresholds'].items():
  t['count_p33']=t.pop('density_low_upper');t['count_p67']=t.pop('density_high_lower');t['count_calibration_windows']=t.pop('density_calibration_windows')
  if t['motion_low_upper']<0:t['motion_low_upper']=None
 return dataset

def verify_derived(d,authoritative):
 zones=[z['id'] for z in d['zones']]; a={(r['window_index'],r['zone_name']):r for r in authoritative}
 prior=0;streaks={z:[0.,0.] for z in zones};n=0
 for w in d['windows']:
  assert w['start_ticks']==prior and w['end_ticks']>prior
  assert w['start_ticks']==w['index']*900000
  assert {r['zone_id'] for r in w['zones']}==set(zones)
  duration=(w['end_ticks']-w['start_ticks'])/90000
  for r in w['zones']:
   z=r['zone_id'];ref=a[(w['index'],z)]; n+=1
   assert abs(w['start_s']-ref['source_start_seconds'])<1e-8
   assert abs(w['end_s']-ref['source_end_seconds_exclusive'])<1e-8
   assert r['count_mean']==ref['mean_current_vehicles']
   assert r['motion_available']==(r['motion_median_px_s'] is not None)
   high=w['complete'] and r['relative_count_level']=='high'
   joint=high and r['motion_low_relative'] is True
   old=streaks[z];now=[old[0]+duration if high else 0.,old[1]+duration if joint else 0.]
   assert abs(r['high_count_duration_s']-now[0])<1e-7 and abs(r['joint_duration_s']-now[1])<1e-7
   streaks[z]=now
   if w['quality']!='complete':assert now==[0.,0.] and r['relative_count_level']=='unknown' and r['motion_median_px_s'] is None
   if r['state_hint']=='high_count_low_motion_persistent': assert now[1]>=30
   if r['state_hint']=='high_count_persistent': assert now[0]>=30
  prior=w['end_ticks']
 assert n==len(a) and prior==round(d['source']['duration_s']*90000)
 return {'passed':True,'zone_window_rows':n,'coverage_ticks':prior,'count_means_exactly_reused':True,'time_boundaries_match_authoritative':True,'persistence_independently_recomputed':True,'null_motion_not_zero':True,'accuracy_evaluated':False}

def new_camera(name,camera):
 start=time.perf_counter();d=R2/'delivery'/camera;a=d/'data_archive/attempt-000001';native=d/'data_archive/output'
 local=read(d/'local_completion.json');server=read(d/'server_camera_completion.json');render=read(d/'render_receipt.json');comp=read(a/'completion.json');summary=read(a/'run_summary.json');pts=read(native/'pts_summary.json');roi=read(R2/'config'/camera/'roi.json')
 assert local['completed'] and local['video_verified'] and server['status']=='completed'
 assert server['split']=='Validation' and roi['source_split']=='Validation'
 source=server['source_id'];assert local['source_id']==render['source_id']==comp['source_id']==roi['source_video']==source
 assert sha(a/'run_summary.json')==comp['files']['run_summary.json']['sha256']
 assert sha(d/'server_camera_completion.json')==local['server_receipt_sha256']
 roi_sha=sha(R2/'config'/camera/'roi.json');assert roi_sha==render['input_hashes']['zones']==summary['roi_sha256']
 assert roi['source_sha256']==server['source_sha256']==render['input_hashes']['source']
 assert (roi['frame_width'],roi['frame_height'],roi['assignment_point'])==(1280,720,'bottom_center')
 assert render['input_hashes']['records']==comp['files']['frames.jsonl']['sha256']
 assert render['output_sha256']==local['files']['annotated.mp4']['sha256']
 zones=[r['zone_name'] for r in roi['zones']];assert len(zones)==len(set(zones))
 E.SOURCE_ID=source
 auth=checked_rows(a/'windows.jsonl',comp['files']['windows.jsonl']['sha256'])
 agg=E.aggregate_frames(audit_stream(a/'frames.jsonl',native/'timing_frames.jsonl',source,comp['fingerprint'],roi_sha,comp['files']['frames.jsonl']['sha256'],pts['timing_frames_sha256'],summary),zones,progress=True)
 # Audit gap is on the NEXT frame; long hold belongs to the preceding presentation interval.
 gaps=rows(native/'pts_gaps.jsonl');track_input(native/'pts_gaps.jsonl',sha(native/'pts_gaps.jsonl'))
 expected={(g['frame_index']-1,g['pts']-g['pts_delta_ticks'],g['pts']) for g in gaps if g['pts_delta_ticks']>45000}
 actual={(h['frame_index'],h['start_ticks'],h['end_ticks']) for h in agg['held_intervals']}
 assert actual==expected,'long hold audit mismatch'
 windows=E.build_windows(auth,agg,zones);thresholds=E.calibrate(windows,zones);E.classify_windows(windows,thresholds)
 result={'source':{'id':source,'sha256':server['source_sha256'],'split':'Validation','duration_s':summary['duration_seconds'],'frame_count':summary['frame_count'],'video_path':str(d/'annotated.mp4'),'video_sha256':render['output_sha256'],'width':1280,'height':720,'video_width':1280,'video_height':870,'content_x':0,'content_y':150,'content_width':1280,'content_height':720},'zones':[{'id':z['zone_name'],'polygon_points':z['polygon_points']} for z in roi['zones']],'windows':windows,'thresholds':thresholds,'held_intervals':agg['held_intervals'],'reused_existing_state':False,'derivation_seconds':time.perf_counter()-start,'source_paths':{'frames':str(a/'frames.jsonl'),'windows':str(a/'windows.jsonl'),'pts_audit':str(native/'pts_summary.json'),'roi':str(R2/'config'/camera/'roi.json')}}
 result=normalize(name,result);result['validation']=verify_derived(result,auth)
 result['validation'].update(frame_pts_checked_against_audit=True,original_frame_hash_verified=True,hold_intervals_match_audit=True,missing_duration_s=(agg['duration_ticks']-sum(agg['valid_ticks'].values()))/90000)
 return result

def reuse_newport():
 start=time.perf_counter();receipt=read(NEWPORT/'generation_receipt.json');d=read(NEWPORT/'traffic_demo.json');rules=read(NEWPORT/'rules_and_thresholds.json')
 assert sha(NEWPORT/'traffic_demo.json')==receipt['output_sha256'] and receipt['status']=='completed'
 assert receipt['all_input_stats_unchanged'] and d['source']['id']==receipt['source_id']
 a=Path(receipt['input_paths']['frames']).parent;comp=read(a/'completion.json');summary=read(a/'run_summary.json')
 n=SYSTEM/'outputs/newport_full_v5_2026-09-24/downloaded_server_results/time_audit_v2';pts=read(n/'pts_summary.json')
 frames=0
 for r in audit_stream(a/'frames.jsonl',n/'timing_frames.jsonl',receipt['source_id'],comp['fingerprint'],receipt['verified_hashes']['roi_sha256'],comp['files']['frames.jsonl']['sha256'],pts['timing_frames_sha256'],summary):
  assert r['duration_ticks']<=45000,'Newport contains held intervals; old state not reusable unchanged';frames+=1
 auth=checked_rows(a/'windows.jsonl',comp['files']['windows.jsonl']['sha256'])
 d.update(thresholds=rules['zones'],held_intervals=[],reused_existing_state=True,source_paths=receipt['input_paths'],derivation_seconds=time.perf_counter()-start)
 # Numerical values and state classifications retained; only field names/null representation standardized.
 d=normalize('Newport',d);d['validation']=verify_derived(d,auth);d['validation'].update(frame_pts_checked_against_audit=True,original_frame_hash_verified=True,hold_intervals_match_audit=True,missing_duration_s=0.,existing_state_sha_verified=True)
 return d

def export(name,d):
 dest=OUT/name;dest.mkdir(exist_ok=True)
 put(dest/'states_10s.json',d)
 put(dest/'rules_and_thresholds.json',{'version':VERSION,'parameters':E.PARAMETERS,'long_hold_seconds':0.5,'hold_policy':'any overlap invalidates whole state window; original count retained; exclude calibration and reset motion history/persistence','thresholds':d['thresholds'],'source':d['source'],'night_case':d['night_case']})
 put(dest/'validation.json',d['validation']);put(dest/'held_intervals.json',d['held_intervals'])
 table=[]
 for w in d['windows']:
  for r in w['zones']:table.append({**{k:v for k,v in w.items() if k!='zones'},**r})
 with (dest/'states_10s.csv').open('w') as f:
  writer=csv.DictWriter(f,fieldnames=list(table[0]));writer.writeheader();writer.writerows(table)
 print(json.dumps({'camera':name,'complete':True,'seconds':d['derivation_seconds'],'held_windows':sum(w['held_duration_s']>0 for w in d['windows']),'states':dict(Counter(r['state_hint'] for w in d['windows'] for r in w['zones']))}),flush=True)

if __name__=='__main__':
 lock=(OUT/'derivation.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if (OUT/'derivation_completion.json').exists():raise SystemExit('Already completed; refusing duplicate derivation')
 verification=read(R2/'final_delivery_verification.json');assert verification['completed']
 total=time.perf_counter();results={}
 results['Newport']=reuse_newport();export('Newport',results['Newport'])
 for name,camera in CAMERAS.items():results[name]=new_camera(name,camera);export(name,results[name])
 for p,s in INPUTS.items():assert (Path(p).stat().st_size,Path(p).stat().st_mtime_ns)==(s['size'],s['mtime_ns'])
 put(OUT/'input_manifest.json',INPUTS)
 put(OUT/'derivation_completion.json',{'completed':True,'version':VERSION,'at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':time.perf_counter()-total,'cameras':list(results),'all_inputs_unchanged':True,'new_yolo':False,'main_test_read':False,'unity_modified':False,'video_exported':False,'implementation_sha256':{p.name:sha(p) for p in (OUT/'code').glob('*.py')}})
