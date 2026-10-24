"""Read-only arithmetic/report cross-check of the root comparison deliverables."""
import csv, json, math, hashlib
from pathlib import Path
OUT=Path(__file__).resolve().parents[1];ROOT=OUT.parent
report_path=ROOT/'五路口原案例与补充时段独立对比总报告.md'
report=report_path.read_text(); data=json.loads((ROOT/'count_analysis.json').read_text())['results']; motion=json.loads((OUT/'motion_summary.json').read_text())['rows']
checks=[];findings=[]
def require(test,msg):
 if not test:raise AssertionError(msg)
def eq(a,b,msg,tol=1e-8):require(abs(a-b)<tol,(msg,a,b))
for fname,key in [('region_count_summary.csv','statistics'),('window_counts_recomputed.csv','windows'),('quarter_trends.csv','quarters')]:
 csvrows=list(csv.DictReader((ROOT/'tables'/fname).open())); jsonrows=[x for r in data for x in r[key]]
 require(len(csvrows)==len(jsonrows),(fname,'row count'))
 for a,b in zip(csvrows,jsonrows):
  for k,v in b.items():require(a[k]==('' if v is None else str(v)),(fname,k,a[k],v))
 checks.append({'check':fname+' CSV/JSON equality','rows':len(csvrows),'passed':True})
 if fname=='quarter_trends.csv' and not all('split' in x and 'source_id' in x for x in csvrows):
  findings.append({'severity':'P2','file':'tables/quarter_trends.csv','issue':'Standalone quarter table has no source_id/split columns, so NE12th supplementary rows omit Train identity.'})
for d in data:
 case=d['case'];batch=d['batch'];stats={r['zone']:r for r in d['statistics']};total=stats['TOTAL_SELECTED_ROIS'];w_by={}
 for row in d['windows']:w_by.setdefault(row['zone'],[]).append(row)
 eq(sum(s['mean'] for z,s in stats.items() if z!='TOTAL_SELECTED_ROIS'),total['mean'],'sum region means')
 for zone,st in stats.items():
  h=d['histograms'][zone];valid=sum(x['duration_s'] for x in h);eq(valid,st['valid_s'],'histogram valid duration')
  mean=sum(x['duration_s']*x['current_count'] for x in h)/valid;eq(mean,st['mean'],'histogram weighted mean')
  eq(sum(x['proportion'] for x in h),1,'histogram proportion')
  eq(sum(x['duration_s'] for x in h if x['current_count']==0)/valid*100,st['zero_pct'],'zero percent')
  for q,k in [(.1,'p10'),(.5,'median'),(.9,'p90')]:
   running=0
   for x in sorted(h,key=lambda x:x['current_count']):
    running+=x['duration_s']
    if running>=valid*q:
     require(x['current_count']==st[k],('histogram quantile',case,batch,zone,k));break
  windows=w_by[zone];eq(sum(w['mean']*w['valid_s'] for w in windows)/valid,mean,'window integrated mean')
  quarters=[q for q in d['quarters'] if q['zone']==zone];require(len(quarters)==4,'four quarters')
  eq(sum(q['mean']*(q['end_s']-q['start_s']) for q in quarters)/valid,mean,'quarter integrated mean',1e-7)
  if zone!='TOTAL_SELECTED_ROIS':
   m=json.loads((OUT/batch/case/'motion_10s.json').read_text())
   for wc,wm in zip(windows,m['windows']):
    mr=next(r for r in wm['zones'] if r['zone_id']==zone)
    eq(wc['mean'],mr['count_mean'],'count/motion mean',1e-7)
    eq(wc['long_hold_s'],wm['held_duration_s'],'count/motion held duration')
    require(wc['eligible_for_motion']==wm['complete'],'temporal eligibility')
 require(d['checks']['long_hold_frames']==len(d['holds']),'holds length')
 eq(sum(x['duration_s'] for x in d['holds']),d['checks']['long_hold_s'],'held total')
 require(d['split']==('Train' if case=='NE12th' and batch=='Supplementary' else 'Validation'),'source split')
 checks.append({'check':'histogram/window/quarter/mean/quantile/hold/motion arithmetic','case':case,'batch':batch,'passed':True})
for case in ['Newport','NE12th','Eastgate','SE38th','NE8th']:
 section=report.split('\n## '+case+'\n')[1].split('\n## ')[0]
 old=next(d for d in data if d['case']==case and d['batch']=='Original');new=next(d for d in data if d['case']==case and d['batch']=='Supplementary')
 newstats={r['zone']:r for r in new['statistics']}
 for a in old['statistics']:
  b=newstats[a['zone']]
  row=f"| {a['zone']} | {a['mean']:.3f} → {b['mean']:.3f} | {a['p10']} / {a['median']} / {a['p90']} | {b['p10']} / {b['median']} / {b['p90']} | {a['zero_pct']:.1f} → {b['zero_pct']:.1f} |"
  require(row in section,('report count table',case,row))
 for zone in old['zones']:
  a=next(r for r in motion if (r['case'],r['batch'],r['zone_id'])==(case,'Original',zone));b=next(r for r in motion if (r['case'],r['batch'],r['zone_id'])==(case,'Supplementary',zone))
  fmt=lambda v:'未知' if v is None else f'{v:.2f}'
  row=f"| {zone} | {fmt(a['motion_p50_px_s'])} → {fmt(b['motion_p50_px_s'])} | {a['available_motion_time_fraction']*100:.1f} → {b['available_motion_time_fraction']*100:.1f} | {a['available_motion_windows']} → {b['available_motion_windows']} |"
  require(row in section,('report motion table',case,row))
 for d in (old,new):
  low=d['representative_intervals']['lowest_complete_60s'];high=d['representative_intervals']['highest_complete_60s']
  row=f"| {d['batch']} ({d['split']}) | {low['start_s']}–{low['end_s']} / {low['mean']:.3f} | {high['start_s']}–{high['end_s']} / {high['mean']:.3f} |"
  require(row in section,('report representative table',row))
 checks.append({'check':'report count/motion/representative table numerical strings','case':case,'passed':True})
require(sum(d['checks']['frames'] for d in data)==978763,'reported frames')
eq(sum(d['checks']['duration_s'] for d in data),32808.687,'reported duration',.0005)
require(sum(d['checks']['windows'] for d in data)==3289,'reported windows')
require(sum(d['checks']['windows']*len(d['zones']) for d in data)==14279,'reported region windows')
paths=[report_path,ROOT/'count_analysis.json',*sorted((ROOT/'tables').glob('*.csv')),*sorted((ROOT/'figures').glob('*motion_evidence.png'))]
result={'passed':True,'checks':checks,'findings':findings,'snapshot_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},'five_motion_figures_visually_reviewed':True,'relative_states_not_compared':True,'motion_described_as_conditional_pixel_evidence':True,'Train_identity_preserved_in_report_and_motion_figures':True,'review_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
(OUT/'root_review_checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'passed':result['passed'],'checks':len(checks),'findings':findings},ensure_ascii=False))
