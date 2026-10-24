"""Independent saved-output checks using standard-library arithmetic and original seals."""
import csv, hashlib, json, math
from pathlib import Path
OUT=Path(__file__).resolve().parents[1]
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 verification=read(OUT/'verification.json');assert verification['completed']
 summary=read(OUT/'motion_summary.json')['rows'];cases=[];total_rows=0
 for p in sorted(OUT.glob('*/*/motion_10s.json')):
  d=read(p);windows=d['windows'];source=d['source'];prior=0
  flat=[]
  for w in windows:
   assert w['start_ticks']==prior and w['end_ticks']>prior;prior=w['end_ticks']
   assert abs(w['start_s']*90000-w['start_ticks'])<1e-6
   assert abs(w['end_s']*90000-w['end_ticks'])<1e-6
   for r in w['zones']:
    assert all(k not in r for k in ('relative_count_level','state_hint','motion_low_relative','traffic_state'))
    assert 0<=r['motion_vehicle_seconds']<=r['observed_vehicle_seconds']+1e-7
    coverage=r['motion_vehicle_seconds']/r['observed_vehicle_seconds'] if r['observed_vehicle_seconds'] else 0
    assert abs(coverage-r['motion_coverage'])<1e-10
    available=w['end_ticks']-w['start_ticks']==900000 and r['missing_duration_s']==0 and w['held_duration_s']==0 and coverage>=.5 and r['motion_vehicle_seconds']>=5
    assert available==r['motion_available']==(r['motion_median_px_s'] is not None)
    if available:assert r['motion_median_px_s']>=0 and math.isfinite(r['motion_median_px_s'])
    flat.append(r)
  assert abs(prior/90000-source['duration_s'])<1e-7
  assert len(list(csv.DictReader((p.parent/'motion_10s.csv').open())))==len(flat)
  total_rows+=len(flat)
  for z in [z['id'] for z in d['zones']]:
   s=next(s for s in summary if (s['case'],s['batch'],s['zone_id'])==(d['case'],d['batch'],z))
   rows=[r for r in flat if r['zone_id']==z];valid=sorted(r['motion_median_px_s'] for r in rows if r['motion_available'])
   assert len(valid)==s['available_motion_windows']
   assert abs(len(valid)*10/source['duration_s']-s['available_motion_time_fraction'])<1e-12
   assert abs(s['unknown_motion_time_fraction']+s['available_motion_time_fraction']-1)<1e-12
   for q,key in [(.25,'motion_p25_px_s'),(.5,'motion_p50_px_s'),(.75,'motion_p75_px_s')]:
    assert s[key]==(valid[math.ceil(len(valid)*q)-1] if valid else None)
   assert abs(sum(r['observed_vehicle_seconds'] for r in rows)-s['observed_vehicle_seconds'])<1e-7
  if d['batch']=='Original':
   old=read(Path(d['reused_file']));assert len(old['windows'])==len(windows)
   for a,b in zip(old['windows'],windows):
    for ra,rb in zip(a['zones'],b['zones']):
     for k in ('motion_median_px_s','motion_available','motion_coverage','motion_vehicle_seconds','observed_vehicle_seconds','motion_track_count'):
      assert ra[k]==rb[k], (d['case'],k)
  if d['case']=='NE12th' and d['batch']=='Supplementary':
   assert source['split']=='Train' and source['duration_s']<409
  cases.append({'case':d['case'],'batch':d['batch'],'source_id':source['id'],'split':source['split'],'rows':len(flat),'passed':True})
 for rel,digest in verification['outputs_sha256'].items():assert sha(OUT/rel)==digest
 assert len(cases)==10 and len(summary)==44 and total_rows==verification['source_zone_window_rows']
 report={'passed':True,'cases':cases,'zone_summary_rows':len(summary),'zone_window_rows':total_rows,'null_motion_preserved':True,'saved_motion_gates_checked_without_engine':True,'summaries_checked_by_order_statistics':True,'original_motion_fields_reused_bit_for_bit':True,'output_hashes_verified':True,'source_PTS_contiguity_and_duration_verified':True,'no_relative_state_fields_in_motion_rows':True,'NE12th_Train_preserved':True,'script_sha256':sha(Path(__file__))}
 (OUT/'independent_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False))
if __name__=='__main__':main()
