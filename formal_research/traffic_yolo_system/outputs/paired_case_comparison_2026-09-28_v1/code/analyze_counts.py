"""Paired completed-source observation analysis, v1. No detector or media imports.

Uses frozen, hashed count exports; checks every timestamp against original integer
PTS audit, and independently reintegrates each region/window. Output is local only.
"""
from pathlib import Path
import argparse, csv, hashlib, io, json, math, os, sys, platform
from datetime import datetime, timezone
from collections import defaultdict
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT.parent
CAMERAS = {'Newport':'Bellevue_150th_Newport', 'NE12th':'Bellevue_116th_NE12th',
 'Eastgate':'Bellevue_150th_Eastgate', 'SE38th':'Bellevue_150th_SE38th', 'NE8th':'Bellevue_Bellevue_NE8th'}
OLD = BASE/'five_intersections_2026-09-27_r2'
NEW = BASE/'supplementary_five_2026-09-28_v3_repair'
INPUTS = {}
TICKS=90000

def put(path, obj):
 path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

def track(path, expected=None):
 path=Path(path); stat=path.stat(); h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 value=h.hexdigest()
 if expected: assert value==expected, (str(path),'sha mismatch')
 INPUTS[str(path)]={'sha256':value,'bytes':stat.st_size,'mtime_ns':stat.st_mtime_ns}
 return value

def read(path):
 track(path); return json.loads(Path(path).read_text())

def dumpcsv(path, rows):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader();w.writerows(rows)

def paths(case, batch):
 if batch=='Supplementary':
  d=NEW/'delivery'/CAMERAS[case]
  return d/'summary_archive/output',d/'full_data_archive/attempt-000001',d/'full_data_archive/output'
 if case=='Newport':
  d=BASE/'newport_full_v5_2026-09-24/downloaded_server_results'
  receipt=read(BASE/'traffic_state_demo_newport_2026-09-27/generation_receipt.json')
  return d/'analysis/server_results',Path(receipt['input_paths']['frames']).parent,d/'time_audit_v2'
 d=OLD/'delivery'/CAMERAS[case]
 return d/'summary_archive/output',d/'data_archive/attempt-000001',d/'data_archive/output'

def quantile(hist,q):
 s=0; total=sum(hist.values())
 for x in sorted(hist):
  s+=hist[x]
  if s+1e-7>=total*q:return int(x)
 return None

def stats(values,weights):
 hist=defaultdict(int)
 for x,w in zip(values,weights):
  if w:hist[int(x)]+=int(w)
 total=sum(hist.values())
 if not total:return dict(mean=None,p10=None,median=None,p90=None,max=None,zero_pct=None)
 return dict(mean=sum(x*w for x,w in hist.items())/total,p10=quantile(hist,.1),median=quantile(hist,.5),p90=quantile(hist,.9),max=max(hist),zero_pct=100*hist.get(0,0)/total)

def compute(case,batch):
 export,attempt,native=paths(case,batch)
 qc=read(export/'qc_summary.json'); comp=read(attempt/'completion.json'); pts_summary=read(native/'pts_summary.json')
 assert qc['status']=='passed' and qc['missing_duration_seconds']==0
 track(export/'frame_counts.csv',qc['exports']['frame_counts.csv']['sha256'])
 track(attempt/'windows.jsonl',comp['files']['windows.jsonl']['sha256'])
 track(native/'timing_frames.jsonl',pts_summary['timing_frames_sha256'])
 rows=list(csv.DictReader((export/'frame_counts.csv').open(encoding='utf-8-sig')))
 zones=list(qc['zones']); n=len(rows)
 start=np.zeros(n,dtype=np.int64); duration=np.zeros(n,dtype=np.int64)
 valid=np.zeros(n,dtype=bool)
 counts=np.array([[int(r[z]) for z in zones]+[int(r['total_in_zones'])] for r in rows],dtype=np.int64)
 assert np.array_equal(counts[:,:-1].sum(axis=1),counts[:,-1])
 cursor=0
 with (native/'timing_frames.jsonl').open() as f:
  for i,r in enumerate(rows):
   q=json.loads(next(f)); tick=int(q.get('duration_ticks',q.get('pkt_duration')))
   assert int(r['frame_index'])==q['frame_index']==i
   assert q['pts']==cursor and tick>0
   assert abs(float(r['source_timestamp_seconds'])-cursor/TICKS)<1e-8
   assert abs(float(r['duration_seconds'])-tick/TICKS)<1e-8
   start[i]=cursor;duration[i]=tick;cursor+=tick
   valid[i]=r['valid']=='True'
  assert not f.readline()
 assert n==qc['totals']['frames']
 assert abs(cursor/TICKS-qc['valid_duration_seconds']-qc['missing_duration_seconds'])<1e-7
 hold=duration>TICKS*.5; weight=np.where(valid,duration,0); nh_weight=np.where(valid & ~hold,duration,0)
 assert abs(weight.sum()/TICKS-qc['valid_duration_seconds'])<1e-7
 # Exact integer presentation-time integration into original 10-second windows.
 nw=(int(cursor)+10*TICKS-1)//(10*TICKS)
 integ=np.zeros((nw,len(zones)+1),dtype=np.int64); wd=np.zeros(nw,dtype=np.int64); held=np.zeros(nw,dtype=np.int64)
 for i in range(n):
  a=int(start[i]);b=a+int(duration[i])
  while a<b:
   k=a//(10*TICKS);e=min(b,(k+1)*10*TICKS);d=e-a
   if valid[i]:integ[k]+=counts[i]*d;wd[k]+=d
   if hold[i]:held[k]+=d
   a=e
 auth=[json.loads(x) for x in (attempt/'windows.jsonl').read_text().splitlines()]
 assert len(auth)==nw*len(zones)
 errors=[]
 source=auth[0]['source_id'];split=auth[0]['split']
 assert split==('Train' if case=='NE12th' and batch=='Supplementary' else 'Validation')
 for r in auth:
  k=r['window_index'];j=zones.index(r['zone_name'])
  assert r['source_id']==source and r['split']==split
  errors.append(abs(integ[k,j]/wd[k]-r['mean_current_vehicles']))
  assert abs(wd[k]/TICKS-r['valid_duration_seconds'])<1e-7
  assert abs(r['source_start_seconds']-k*10)<1e-7
  assert abs(r['source_end_seconds_exclusive']-min((k+1)*10,cursor/TICKS))<1e-7
 assert max(errors)<1e-8
 records=[];histograms={};window_records=[];quarters=[]
 for j,z in enumerate(zones+['TOTAL_SELECTED_ROIS']):
  st=stats(counts[:,j],weight); clean=stats(counts[:,j],nh_weight)
  expected=qc['total_selected_road_current_count']['mean_current_vehicles'] if j==len(zones) else qc['zones'][z]['mean_current_vehicles']
  assert abs(st['mean']-expected)<1e-8
  record=dict(case=case,batch=batch,source_id=source,split=split,zone=z,duration_s=cursor/TICKS,valid_s=float(weight.sum()/TICKS),missing_s=float((cursor-weight.sum())/TICKS),held_s=float(duration[hold].sum()/TICKS),**st,mean_excluding_long_hold_frames=clean['mean'])
  records.append(record)
  h=defaultdict(int)
  for x,w in zip(counts[:,j],weight): h[int(x)]+=int(w)
  histograms[z]=[dict(current_count=x,duration_s=t/TICKS,proportion=t/weight.sum()) for x,t in sorted(h.items())]
  for k in range(nw):
   end=min((k+1)*10,cursor/TICKS)
   window_records.append(dict(case=case,batch=batch,source_id=source,split=split,zone=z,window_index=k,start_s=k*10,end_s=end,duration_s=end-k*10,valid_s=float(wd[k]/TICKS),long_hold_s=float(held[k]/TICKS),eligible_for_motion=bool(held[k]==0 and abs(end-k*10-10)<1e-8 and wd[k]==10*TICKS),mean=float(integ[k,j]/wd[k]) if wd[k] else None))
  for q in range(4):
   a=round(cursor*q/4);b=round(cursor*(q+1)/4)
   qw=np.where(valid,np.maximum(0,np.minimum(start+duration,b)-np.maximum(start,a)),0)
   quarters.append(dict(case=case,batch=batch,source_id=source,split=split,zone=z,quarter=q+1,start_s=a/TICKS,end_s=b/TICKS,mean=float(np.dot(counts[:,j],qw)/qw.sum())))
 holds=[dict(frame_index=int(i),start_s=float(start[i]/TICKS),end_s=float((start[i]+duration[i])/TICKS),duration_s=float(duration[i]/TICKS)) for i in np.flatnonzero(hold)]
 totals=[r for r in window_records if r['zone']=='TOTAL_SELECTED_ROIS']
 candidates=[]
 for k in range(len(totals)-5):
  seg=totals[k:k+6]
  if all(r['eligible_for_motion'] for r in seg):candidates.append(dict(start_s=seg[0]['start_s'],end_s=seg[-1]['end_s'],mean=sum(r['mean'] for r in seg)/6))
 exemplars={}
 if candidates:
  exemplars={'lowest_complete_60s':min(candidates,key=lambda r:r['mean']),'highest_complete_60s':max(candidates,key=lambda r:r['mean'])}
 ver=dict(case=case,batch=batch,source_id=source,split=split,frames=n,regions=len(zones),duration_s=cursor/TICKS,missing_s=float((cursor-weight.sum())/TICKS),long_hold_frames=len(holds),long_hold_s=sum(h['duration_s'] for h in holds),long_hold_max_s=max([h['duration_s'] for h in holds],default=0),hold_affected_10s_windows=int((held>0).sum()),windows=nw,eligible_windows=sum(r['eligible_for_motion'] for r in totals),max_10s_reintegration_error=max(errors),csv_sha_matches_qc=True,windows_sha_matches_completion=True,pts_sha_matches_native_audit=True,all_integer_pts_and_durations_match=True,count_sum_consistent=True,qc_means_match=True)
 return dict(case=case,batch=batch,source_id=source,split=split,zones=zones,statistics=records,histograms=histograms,windows=window_records,quarters=quarters,holds=holds,representative_intervals=exemplars,checks=ver)

def plot(all_results,dest):
 os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'code/.mpl_cache'))
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False,'figure.facecolor':'white'})
 colors={'Original':'#2166AC','Supplementary':'#D6604D'}
 dest.mkdir(parents=True,exist_ok=True)
 for case in CAMERAS:
  pair=[r for r in all_results if r['case']==case];zones=pair[0]['zones']
  assert zones==pair[1]['zones']
  fig,axes=plt.subplots(len(zones)+1,2,figsize=(14,2.4*(len(zones)+1)),gridspec_kw={'width_ratios':[1.6,1]})
  for row,z in enumerate(zones+['TOTAL_SELECTED_ROIS']):
   a,b=axes[row]
   for r in pair:
    batch=r['batch'];c=colors[batch];w=[v for v in r['windows'] if v['zone']==z]
    label=batch+(' (Train, 6.81 min)' if r['split']=='Train' else ' (Validation)')
    # Include retained displayed counts, explicitly shade held image evidence.
    a.plot([(v['start_s']+v['end_s'])/120 for v in w if v['duration_s']>9.99],[v['mean'] for v in w if v['duration_s']>9.99],lw=.9,color=c,label=label)
    for h in r['holds']:a.axvspan(h['start_s']/60,h['end_s']/60,color=c,alpha=.18)
    hist=r['histograms'][z];b.step([x['current_count'] for x in hist],np.cumsum([x['proportion'] for x in hist]),where='post',color=c,lw=1.7,label=label)
   a.set(title=z.replace('_',' '),xlabel='Original relative PTS (min)',ylabel='10 s mean current count (vehicles)')
   b.set(title='Time-weighted count distribution',xlabel='Current count (vehicles)',ylabel='Cumulative valid display time',ylim=(0,1.03))
   a.grid(alpha=.17);b.grid(alpha=.17)
   if row==0:a.legend(loc='upper right',fontsize=8);b.legend(loc='lower right',fontsize=8)
  caveat='Same named image regions; descriptive model observations, not congestion or flow.'
  if case=='NE12th':caveat='Night Validation (60 min) vs daylight Train (6.81 min): unequal duration and split; not independent validation.'
  if case=='NE8th':caveat='Day vs night: zero detections do not establish an empty road; motion evidence can be absent.'
  if case=='Newport':caveat='Same ROI and numeric filter rules; only sparse source review. Filtering accuracy is unverified.'
  fig.suptitle(f'{case} | Original and supplementary sources\n{caveat}',fontsize=12,y=.997)
  fig.text(.02,.007,'Shading: original frame presentation > 0.5 s (retained counts, no fresh image evidence). Source clocks are not aligned. CDF is not a confidence interval.',fontsize=8)
  fig.tight_layout(rect=[0,.025,1,.96]);fig.savefig(dest/f'{case}_counts_and_distribution.png',dpi=160);fig.savefig(dest/f'{case}_counts_and_distribution.pdf');plt.close(fig)
 # Fixed source-list order, never ranked; no common scale across cameras.
 fig,axes=plt.subplots(5,1,figsize=(11,11))
 for ax,case in zip(axes,CAMERAS):
  pair=[r for r in all_results if r['case']==case];zones=pair[0]['zones'];x=np.arange(len(zones))
  for offset,r in zip([-.16,.16],pair):
   st=[s for s in r['statistics'] if s['zone']!='TOTAL_SELECTED_ROIS'];values=[s['mean'] for s in st]
   bars=ax.bar(x+offset,values,width=.3,color=colors[r['batch']],label=r['batch']+(' (Train)' if r['split']=='Train' else ' (Validation)'))
   ax.bar_label(bars,fmt='%.2f',fontsize=8,padding=2)
  ax.set_xticks(x,zones);ax.set_ylabel('Mean count (vehicles)');ax.set_title(case,loc='left');ax.margins(y=.3);ax.legend(fontsize=8)
 fig.suptitle('Paired observations in same named regions\nOriginal PTS weighting; separate camera scales; not a congestion ranking',fontsize=13)
 fig.tight_layout(rect=[0,0,1,.95]);fig.savefig(dest/'paired_region_means.png',dpi=160);fig.savefig(dest/'paired_region_means.pdf');plt.close(fig)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT);args=parser.parse_args();out=args.output.resolve()
 if (out/'count_analysis.json').exists():raise SystemExit('Output already exists. Use --output with a fresh directory.')
 out.mkdir(parents=True,exist_ok=True)
 all_results=[]
 for case in CAMERAS:
  for batch in ['Original','Supplementary']:
   r=compute(case,batch);all_results.append(r);print(json.dumps(r['checks']),flush=True)
 dumpcsv(out/'tables/region_count_summary.csv',[s for r in all_results for s in r['statistics']])
 dumpcsv(out/'tables/window_counts_recomputed.csv',[s for r in all_results for s in r['windows']])
 dumpcsv(out/'tables/quarter_trends.csv',[s for r in all_results for s in r['quarters']])
 put(out/'count_analysis.json',{'version':'paired-observation-v1','time_base':{'num':1,'den':TICKS},'long_hold_threshold_s':.5,'weighted_quantile_method':'inverse time-weighted empirical CDF (no interpolation)','motion_and_states_calibrated':False,'results':all_results})
 plot(all_results,out/'figures')
 for p,s in INPUTS.items():
  st=Path(p).stat();assert(st.st_size,st.st_mtime_ns)==(s['bytes'],s['mtime_ns'])
 put(out/'count_input_manifest.json',INPUTS)
 put(out/'count_verification.json',{'passed':True,'created_at':datetime.now(timezone.utc).isoformat(),'cases':[r['checks'] for r in all_results],'input_file_stats_unchanged':True,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'python':sys.version,'numpy':np.__version__,'platform':platform.platform(),'original_outputs_modified':False,'accuracy_evaluated':False})

if __name__=='__main__':main()
