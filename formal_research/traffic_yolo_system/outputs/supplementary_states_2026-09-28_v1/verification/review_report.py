"""Read-only numerical and example cross-check of the Chinese research report."""
import json,hashlib
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parent
REPORT=ROOT/'补充五路口交通状态与证据不足研究报告.md'
text=REPORT.read_text();index=json.loads((ROOT/'results_index.json').read_text())['results'];summary=json.loads((ROOT/'region_summary.json').read_text());events=json.loads((ROOT/'hint_events.json').read_text())
count_rows=0;example_rows=0
for ix in index:
 case=ix['case'];part=text.split('\n## '+case+'\n')[1].split('\n## ')[0]
 row=f"| {case} | {ix['split']} / {ix['duration_s']:.3f}s | {ix['complete_unheld_windows']}/{ix['windows']} | {ix['count_calibrated_regions']}/{ix['motion_calibrated_regions']}（共{ix['regions']}区） | {ix['high_count_hint_region_windows']}/{ix['joint_hint_region_windows']} | {ix['unknown_evidence_region_windows']}/{ix['all_region_windows']} |"
 assert row in text,(case,'overview')
 for r in [r for r in summary if r['case']==case]:
  p25='null／不足' if r['motion_p25_px_s'] is None else f"{r['motion_p25_px_s']:.4f}"
  row=f"| {r['zone_id']} | {r['count_p33']:.4f} / {r['count_p67']:.4f} | {r['count_calibration_windows']} | {p25} | {r['motion_calibration_windows']} | {r['high_count_hint_windows']} / {r['joint_hint_windows']} | {r['evidence_unknown_fraction']*100:.2f} |"
  assert row in part,(case,'threshold table',row);count_rows+=1
 examples=json.loads((ROOT/case/'examples.json').read_text())['examples']
 for label,r in examples.items():
  if r is None:continue
  row=f"| {label} | {r['zone_id']} | [{r['start_s']:.3f}, {r['end_s']:.6f}) | {r['state_hint']} / {r['evidence_state']} | {r['high_count_duration_s']:.1f} / {r['joint_duration_s']:.1f} |"
  assert row in part,(case,'example table',row);example_rows+=1
for label,confirm,case in [('390,400',420,'Newport'),('900–930',930,'Eastgate'),('330–360',360,'SE38th')]:
 e=min((e for e in events if e['case']==case and e['hint_type']=='joint'),key=lambda e:e['first_confirmation_s'])
 assert e['first_confirmation_s']==confirm and e['condition_start_s']==confirm-30
assert sum(r['frames'] for r in index)==443508
assert round(sum(r['duration_s'] for r in index),3)==14808.639
assert sum(r['windows'] for r in index)==1485 and sum(r['complete_unheld_windows'] for r in index)==1474
assert sum(r['all_region_windows'] for r in index)==6342
assert sum(r['high_count_hint_windows'] for r in summary)==472 and sum(r['joint_hint_windows'] for r in summary)==52
assert sum(r['evidence_unknown_windows'] for r in summary)==3798 and sum(r['high_count_hints_with_unknown_evidence'] for r in summary)==41
assert sum(e['hint_type']=='high_count' for e in events)==212 and sum(e['hint_type']=='joint' for e in events)==35
holds=json.loads((ROOT/'SE38th/states_10s.json').read_text())['held_intervals']
for h in holds:
 assert f"{h['start_ticks']/90000:.3f}–{h['end_ticks']/90000:.3f}" in text
assert '任一条件存在便为unknown' in text and '不能让冻结state_hint覆盖灰色unknown' in text
assert '不是固定基准迁移或外推准确率验证' in text and '最后一种也不代表畅通' in text
result={'passed':True,'overview_rows':5,'threshold_and_hint_summary_rows':count_rows,'example_rows':example_rows,'reported_aggregate_totals_checked':True,'first_joint_confirmation_and_condition_intervals_checked':True,'SE38th_exact_held_intervals_checked':True,'unknown_vs_frozen_state_and_no_cross_source_claim_reviewed':True,'findings':[],'report_sha256':hashlib.sha256(REPORT.read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
(OUT/'report_review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
