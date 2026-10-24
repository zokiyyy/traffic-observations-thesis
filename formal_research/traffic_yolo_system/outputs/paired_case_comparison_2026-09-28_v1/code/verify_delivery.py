"""Cross-module numeric/provenance/artifact checks, independent of derivation engine."""
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
import csv,hashlib,json,re
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT.parent

def read(p):return json.loads(p.read_text())
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(2**20),b''):h.update(b)
 return h.hexdigest()
def put(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
checks=[]
def check(v,label):
 assert v,label
 checks.append(label)
counts=read(ROOT/'count_analysis.json')['results']
ms=read(ROOT/'motion/motion_summary.json')['rows']
check(read(ROOT/'count_verification.json')['passed'],'count_verification_passed')
check(read(ROOT/'audit/audit_checks.json')['passed'],'audit_verification_passed')
check(read(ROOT/'motion/independent_verification.json')['passed'],'independent_motion_verification_passed')
check(read(ROOT/'evidence/evidence_metadata.json')['all_checks_passed'],'visual_evidence_receipts_passed')
for r in counts:
 c,b=r['case'],r['batch'];prefix=f'{c}/{b}'
 motion=read(ROOT/f'motion/{b}/{c}/motion_10s.json')
 check(motion['source']['id']==r['source_id'],prefix+' same source id')
 check(motion['source']['split']==r['split'],prefix+' same split')
 windows=motion['windows'];cw={(w['window_index'],w['zone']):w for w in r['windows']}
 check(len(windows)==r['checks']['windows'],prefix+' same window count')
 for w in windows:
  z0=cw[w['index'],r['zones'][0]]
  check(abs(w['held_duration_s']-z0['long_hold_s'])<1e-7,prefix+f' window {w["index"]} held overlap')
  check(w['complete']==z0['eligible_for_motion'],prefix+f' window {w["index"]} temporal eligibility')
  for z in w['zones']:
   q=cw[w['index'],z['zone_id']]
   assert abs(q['mean']-z['count_mean'])<1e-8
   if not q['eligible_for_motion']:assert z['motion_median_px_s'] is None
 for s in r['statistics']:
  z=s['zone'];hist=r['histograms'][z];ts=sum(h['duration_s'] for h in hist)
  check(abs(ts-s['valid_s'])<1e-7,prefix+'/'+z+' histogram mass')
  check(abs(sum(h['current_count']*h['duration_s'] for h in hist)/ts-s['mean'])<1e-10,prefix+'/'+z+' histogram moment')
  for q,key in [(.1,'p10'),(.5,'median'),(.9,'p90')]:
   below=sum(h['duration_s'] for h in hist if h['current_count']<s[key])/ts
   at=sum(h['duration_s'] for h in hist if h['current_count']<=s[key])/ts
   check(below<q+1e-12 and at>=q-1e-12,prefix+'/'+z+' '+key+' quantile bracket')
  qw=[w for w in r['windows'] if w['zone']==z]
  check(abs(sum(w['mean']*w['valid_s'] for w in qw)/s['valid_s']-s['mean'])<1e-10,prefix+'/'+z+' whole/window weighted mean')
  quarters=[v for v in r['quarters'] if v['zone']==z]
  check(abs(sum(v['mean']*(v['end_s']-v['start_s']) for v in quarters)/s['valid_s']-s['mean'])<1e-9,prefix+'/'+z+' quarter weighted mean')
 check(r['checks']['missing_s']==0,prefix+' no missing output records')
check(len(ms)==44,'44 region-source motion summaries')
check(sum(r['checks']['frames'] for r in counts)==978763,'978763 frame counts checked')

# Consolidate each module's independently computed hashes. Conflicts are failures.
manifest={}
def add(path,meta,origin):
 p=Path(path); key=str(p.resolve());item=manifest.setdefault(key,{'sha256':meta['sha256'],'bytes':meta.get('bytes',meta.get('size')),'modules':[]})
 assert item['sha256']==meta['sha256'],(key,'inconsistent module SHA')
 if item['bytes'] is None:item['bytes']=p.stat().st_size
 if 'mtime_ns' in meta:
  assert p.stat().st_mtime_ns==meta['mtime_ns'],(key,'input mtime changed')
 if origin not in item['modules']:item['modules'].append(origin)
 assert p.is_file() and p.stat().st_size==item['bytes'],(key,'missing/resized input')
for fn in ['count_input_manifest.json','motion/input_manifest.json']:
 for p,m in read(ROOT/fn).items():add(p,m,fn)
for x in read(ROOT/'audit/audit_input_hashes.json')['files']:add(x['path'],x,'audit')
for p,m in read(ROOT/'evidence/evidence_metadata.json')['input_hashes'].items():add(p,m,'evidence')
required=[BASE/'five_intersections_2026-09-27_r2/五路口完整结果与说明.md',BASE/'five_intersections_2026-09-27_r2/five_video_results_index.json',BASE/'five_intersections_2026-09-27_r2/final_delivery_verification.json',BASE/'supplementary_five_2026-09-28_v3_repair/补充批次完整结果与说明.md',BASE/'supplementary_five_2026-09-28_v3_repair/supplementary_results_index.json',BASE/'supplementary_five_2026-09-28_v3_repair/final_delivery_verification.json',BASE/'five_intersection_states_2026-09-28_v1/五路口交通状态分析总报告.md',BASE/'persistence_sensitivity_2026-09-28/持续时间门槛敏感性分析.md']
for p in required:add(p,{'sha256':sha(p),'bytes':p.stat().st_size},'required source document')
small_rehashed=0
for name,m in manifest.items():
 if m['bytes']<20*1024*1024:
  assert sha(Path(name))==m['sha256'],(name,'final rehash mismatch');small_rehashed+=1
put(ROOT/'input_hashes.json',{'scope':'Module-computed SHA-256, conflicts checked, small inputs rehashed at finalization; large input hashes already verified by the audited modules this session. No Main Test image/label inputs.','files':manifest,'count':len(manifest)})

links=0
for p in list(ROOT.glob('*.md'))+list((ROOT/'cases').glob('*.md')):
 for target in re.findall(r'\]\(([^)]+)\)',p.read_text()):
  if target.startswith('http'):continue
  q=Path(target) if target.startswith('/') else p.parent/target
  if q.name=='final_verification.json':continue
  check(q.exists(),p.name+' link '+q.name);links+=1
figure_paths=list((ROOT/'figures').glob('*.png'))
check(len(figure_paths)==12,'12 English analysis figures')
for p in figure_paths:
 check(p.read_bytes()[:8]==b'\x89PNG\r\n\x1a\n' and p.stat().st_size>10000,p.name+' valid PNG artifact')
 check(p.with_suffix('.pdf').read_bytes().startswith(b'%PDF'),p.name+' PDF export exists')
for p in ROOT.rglob('*.csv'):
 with p.open() as f:
  rows=list(csv.DictReader(f));assert rows,(p,'empty CSV')

# Exclude volatile interpreter caches; outputs and reproducibility sources remain hashed.
output_hashes={str(p.relative_to(ROOT)):{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(ROOT.rglob('*')) if p.is_file() and p.name!='final_verification.json' and not any(part.startswith('.') or part=='__pycache__' for part in p.relative_to(ROOT).parts)}
put(ROOT/'final_verification.json',{'completed':True,'created_at':datetime.now(timezone.utc).isoformat(),'version':'paired-observation-v1','source_count':10,'case_pairs':5,'frames_checked':978763,'source_windows':3289,'region_windows':14279,'cross_module_check_count':len(checks),'all_cross_module_checks_passed':True,'input_hash_count':len(manifest),'small_input_final_rehash_count':small_rehashed,'module_hash_conflicts':0,'checked_report_links':links,'analysis_figures_png_pdf':12,'checks':checks,'output_files':output_hashes,'limitations':'Engineering consistency, provenance and descriptive observations only; no traffic-state/count accuracy evaluation.','main_test_images_labels_read':False,'new_inference':False,'original_outputs_modified':False})
print(json.dumps({'completed':True,'checks':len(checks),'input_hashes':len(manifest),'artifacts_hashed':len(output_hashes),'links':links}))
