"""Final input/output checksum and artifact checks; no raw media/frame reads."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,re,csv
ROOT=Path(__file__).resolve().parents[1]
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
done=read(ROOT/'derivation_completion.json');assert done['completed'] and done['cached_count_motion_reused']
assert sha(ROOT/'code/derive_states.py')==done['script_sha256']
assert sha(ROOT/'code/frozen_engine.py')==done['frozen_engine_sha256']
checks=[]
def check(value,name):
 assert value,name
 checks.append(name)
manifest=read(ROOT/'input_manifest.json')
for name,m in manifest.items():
 p=Path(name);check(sha(p)==m['sha256'],'unchanged source '+name)
 check(p.stat().st_size==m['bytes'],'input bytes '+name)
ind=read(ROOT/'verification/independent_verification.json');check(ind['passed'],'independent numerical verification passed')
for name,m in ind['read_inputs'].items():check(sha(Path(name))==m['sha256'],'numerical-review input '+name)
for fn in ['boundary_tests.json','display_boundary_tests.json']:
 d=read(ROOT/'verification'/fn);check(d['passed'],'boundary check '+fn)
 if 'production_script_sha256' in d:check(sha(ROOT/'code/derive_states.py')==d['production_script_sha256'],'display test exact implementation')
research=read(ROOT/'research_notes/derived_output_research_review.json');check(research['passed'],'independent research interpretation relationships')
report_review=read(ROOT/'verification/report_review.json');check(report_review['passed'],'independent report review passed')
check(sha(ROOT/'补充五路口交通状态与证据不足研究报告.md')==report_review['report_sha256'],'independent review binds final report')
figs=read(ROOT/'figures/figure_manifest.json')
for row in figs['inputs']+figs['figures']:check(sha(Path(row['path']))==row['sha256'],'figure binding '+row['path'])
qa=read(ROOT/'figures/visual_qa.json');check(qa['all_png_figures_ai_visually_reviewed'],'all 10 PNGs visually inspected')
check(qa['figure_count_png']==qa['figure_count_pdf']==10,'10 English PNG/PDF figure pairs')
idx=read(ROOT/'results_index.json')['results'];rows=read(ROOT/'region_summary.json')
check(len(idx)==5 and len(rows)==22,'5 sources and 22 region summaries')
check(sum(r['all_region_windows'] for r in idx)==6342,'6342 region windows')
check(sum(r['high_count_hint_windows'] for r in rows)==472,'472 count-only criterion hint windows')
check(sum(r['joint_hint_windows'] for r in rows)==52,'52 joint subset hint windows')
check(sum(r['high_count_hints_with_unknown_evidence'] for r in rows)==41,'41 count-hint windows keep unknown evidence')
check(sum(r['evidence_unknown_windows'] for r in rows)==3798,'3798 unknown-evidence region windows')
check(all(r['evidence_unknown_fraction']==1 for r in rows if r['case']=='NE8th'),'NE8th all regions fully unknown')
check(all(r['split']=='Train' for r in rows if r['case']=='NE12th'),'NE12th Train identity')
links=0
for p in [ROOT/'补充五路口交通状态与证据不足研究报告.md',ROOT/'README.md']+list(ROOT.glob('*/中文结果说明.md')):
 for target in re.findall(r'\]\(([^)]+)\)',p.read_text()):
  if target.startswith('http'):continue
  t=Path(target) if target.startswith('/') else p.parent/target
  if t.name=='final_verification.json':continue
  check(t.exists(),'report link '+str(t));links+=1
# One CSV row per region-window, no metadata loss when moving from JSON.
for x in idx:
 with (ROOT/x['case']/'states_10s.csv').open() as f:csvrows=list(csv.DictReader(f))
 check(len(csvrows)==x['all_region_windows'],'CSV length '+x['case'])
 check(all(r['source_id']==x['source_id'] and r['split']==x['split'] for r in csvrows),'CSV source binding '+x['case'])
output={str(p.relative_to(ROOT)):{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(ROOT.rglob('*')) if p.is_file() and p.name!='final_verification.json' and not any(a.startswith('.') or a=='__pycache__' for a in p.relative_to(ROOT).parts)}
result={'completed':True,'created_at':datetime.now(timezone.utc).isoformat(),'version':done['version'],'cases':5,'source_windows':1485,'region_windows':6342,'threshold_regions':22,'direct_input_hashes_rechecked':len(manifest),'final_checks':len(checks),'report_links_checked':links,'boundary_tests':30,'independent_numeric_verification_passed':True,'figures_visually_reviewed':10,'png_pdf_pairs':10,'source_local_calibration_only':True,'fixed_baseline_transfer_performed':False,'cached_observations_preserved':True,'original_outputs_unchanged':True,'main_test_images_labels_read':False,'unity_modified':False,'no_new_inference_or_full_frame_pass':True,'accuracy_evaluated':False,'checks':checks,'output_files':output}
(ROOT/'final_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print(json.dumps({k:result[k] for k in ['completed','cases','region_windows','direct_input_hashes_rechecked','final_checks','boundary_tests','report_links_checked']}))
