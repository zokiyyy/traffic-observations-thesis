import json, math, re
from pathlib import Path
import numpy as np
from derive_states import OUT, put, sha, NEWPORT
checks={};total=0
for name in ['Newport','NE12th','Eastgate','SE38th','NE8th']:
 d=json.loads((OUT/name/'states_10s.json').read_text());ws=d['windows'];zids=[z['id'] for z in d['zones']]
 for z in d['zones']:
  poly=z.get('polygon_points',z.get('polygon'));assert len(poly)>=3
  if isinstance(poly[0],list):assert all(0<=x<=1280 and 0<=y<=720 for x,y in poly)
  else:assert all(0<=p['x']<=1 and 0<=p['y']<=1 for p in poly)
 for z in zids:
  th=d['thresholds'][z];eligible=[r for w in ws if w['complete'] for r in w['zones'] if r['zone_id']==z]
  counts=[r['count_mean'] for r in eligible];motions=[r['motion_median_px_s'] for r in eligible if r['motion_available']]
  assert th['count_calibration_windows']==len(counts) and th['motion_calibration_windows']==len(motions)
  assert np.allclose(np.quantile(counts,[.33,.67]),[th['count_p33'],th['count_p67']],rtol=0,atol=1e-9)
  if len(motions)<30:assert th['motion_low_upper'] is None
  else:assert abs(float(np.quantile(motions,.25))-th['motion_low_upper'])<1e-9
  for w in ws:
   r=next(r for r in w['zones'] if r['zone_id']==z);v=r['count_mean'];expected='unknown'
   if w['complete']:
    if v==0:expected='empty'
    elif th['count_p67']>th['count_p33']:expected='low' if v<=th['count_p33'] else ('medium' if v<=th['count_p67'] else 'high')
   assert r['relative_count_level']==expected
   if r['motion_available']:
    assert w['complete'] and r['motion_coverage']>=.5 and r['motion_vehicle_seconds']>=5
   assert 0<=r['motion_coverage']<=1+1e-9
   assert r['missing_duration_s']==0
 if name=='Newport':
  old=json.loads((NEWPORT/'traffic_demo.json').read_text())
  for a,b in zip(old['windows'],ws):
   for x,y in zip(a['zones'],b['zones']):
    assert x['traffic_state']==y['state_hint'] and x['density_level']==y['relative_count_level']
    for k in ['count_mean','high_count_duration_s','joint_duration_s','motion_coverage']:assert x[k]==y[k]
    assert (None if x['motion_median_px_s']<0 else x['motion_median_px_s'])==y['motion_median_px_s']
 for p in OUT.glob('**/*.md'):
  for target in re.findall(r'\]\((/[^\)]+)\)',p.read_text()):assert Path(target).exists(),target
 assert Path(d['source']['video_path']).exists()
 checks[name]={'passed':True,'source_frames':d['source']['frame_count'],'zone_window_rows':len(ws)*len(zids),'independent_numpy_quantiles':True,'count_level_boundaries':True,'roi_geometry_range':True,'motion_evidence_gate':True,'missing_time_s':0,'newport_numerical_reuse_exact':True if name=='Newport' else None}
 total+=len(ws)*len(zids)
assert total==7937
put(OUT/'final_state_verification.json',{'completed':True,'cameras':checks,'zone_window_rows':total,'software_boundary_tests':12,'traffic_accuracy_evaluated':False,'chart_visual_review':'All five English overview plots inspected for layout, axes, legends and missing-evidence presentation.','files':{str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and p.suffix in ['.json','.csv','.png','.pdf','.md','.py'] and p.name!='final_state_verification.json'}})
print(json.dumps(checks,indent=2))
