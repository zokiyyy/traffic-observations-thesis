#!/usr/bin/env python3
"""Independent small-input calibration review; never reads frames or videos.

Only cached 10-second motion/count summaries, frozen rules, reports and their
receipts are read. No model/decoder/Unity code is imported or executed.
"""
import hashlib
import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent
BASE = OUT.parents[1]
OLD = BASE/'five_intersection_states_2026-09-28_v1'
PAIRED = BASE/'paired_case_comparison_2026-09-28_v1'
SENS = BASE/'persistence_sensitivity_2026-09-28'
INPUTS = {}
CHECKS = []

def record(path, expected=None):
    path = path.resolve()
    assert path.stat().st_size < 8_000_000, 'This review only accepts small inputs'
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    INPUTS[str(path)] = {'sha256': actual, 'bytes': len(raw), 'expected_sha256': expected}
    if expected is not None:
        CHECKS.append({'check': f'hash {path}', 'passed': actual == expected})
        assert actual == expected
    return raw

def read(path, expected=None):
    return json.loads(record(path, expected))

def quantile(values, p):
    values = sorted(values)
    assert values
    x = (len(values)-1)*p
    a, b = math.floor(x), math.ceil(x)
    return values[a]*(1-(x-a)) + values[b]*(x-a)

def check(name, value):
    CHECKS.append({'check': name, 'passed': bool(value)})
    assert value, name

final_old = read(OLD/'final_state_verification.json')
check('original state verification completed', final_old['completed'])
for name in ['五路口交通状态分析总报告.md', 'code/engine.py', 'code/derive_states.py']:
    record(OLD/name, final_old['files'][name])
final_pair = read(PAIRED/'final_verification.json')
check('paired verification completed', final_pair['completed'] and final_pair['all_cross_module_checks_passed'])
pair_files = final_pair['output_files']
record(PAIRED/'五路口原案例与补充时段独立对比总报告.md',
       pair_files['五路口原案例与补充时段独立对比总报告.md']['sha256'])
motion_check = read(PAIRED/'motion/verification.json', pair_files['motion/verification.json']['sha256'])
motion_independent = read(PAIRED/'motion/independent_verification.json',
                          pair_files['motion/independent_verification.json']['sha256'])
check('existing motion independent verification passed', motion_independent['passed'])
check('existing motion production completed', motion_check['completed'])
record(SENS/'持续时间门槛敏感性分析.md')
sensitivity = read(SENS/'results.json')
check('30 second sensitivity baseline matched', sensitivity['completed'] and
      sensitivity['reference_30s_matches_all_existing_streaks_and_alert_hints'])
for name, entry in sensitivity['input_hashes'].items():
    record(Path(entry['path']), entry['sha256'])

cases = []
for case in ['Newport', 'NE12th', 'Eastgate', 'SE38th', 'NE8th']:
    rel = f'Supplementary/{case}/motion_10s.json'
    d = read(PAIRED/'motion'/rel, motion_check['outputs_sha256'][rel])
    complete = [w for w in d['windows'] if w['complete']]
    check(f'{case}: complete definition', all(w['quality'] == 'complete' and
          w['held_duration_s'] == 0 and abs(w['end_s']-w['start_s']-10) < 1e-8 for w in complete))
    zones = []
    for z in [r['zone_id'] for r in complete[0]['zones']]:
        rows = [r for w in complete for r in w['zones'] if r['zone_id'] == z]
        counts = [r['count_mean'] for r in rows]
        motions = [r['motion_median_px_s'] for r in rows if r['motion_available']]
        check(f'{case}/{z}: valid cached motion is finite', all(v is not None and math.isfinite(v) and v >= 0 for v in motions))
        p33, p67 = quantile(counts, .33), quantile(counts, .67)
        zones.append({'zone_id': z, 'count_calibration_windows': len(counts),
                      'zero_count_windows': counts.count(0),
                      'zero_count_window_fraction': counts.count(0)/len(counts),
                      'count_p33': p33, 'count_p67': p67,
                      'count_thresholds_distinguishable': p67 > p33,
                      'motion_calibration_windows': len(motions),
                      'motion_p25_available': len(motions) >= 30,
                      'motion_p25_px_s': quantile(motions, .25) if len(motions) >= 30 else None})
    cases.append({'case': case, 'source': d['source'], 'total_windows': len(d['windows']),
                  'complete_no_hold_windows': len(complete),
                  'hold_intersecting_windows': sum(w['held_duration_s'] > 0 for w in d['windows']),
                  'zones': zones})

ne8 = next(x for x in cases if x['case'] == 'NE8th')
ne12 = next(x for x in cases if x['case'] == 'NE12th')
check('NE8th all quantity thresholds collapse to zero', all(z['count_p33'] == z['count_p67'] == 0 for z in ne8['zones']))
check('NE8th road_top motion remains eligible for P25', next(z for z in ne8['zones'] if z['zone_id'] == 'road_top')['motion_calibration_windows'] == 34)
check('NE12th Train identity and 36 eligible windows', ne12['source']['split'] == 'Train' and ne12['complete_no_hold_windows'] == 36)
check('NE12th only road_right motion calibration available', [z['zone_id'] for z in ne12['zones'] if z['motion_p25_available']] == ['road_right'])
record(Path(__file__))
(OUT/'calibration_scope_probe.json').write_text(json.dumps({
    'method': 'Independent linear P33/P67/P25 from existing complete no-hold 10-second summaries; not an independent traffic-state evaluation.',
    'full_video_or_frame_logs_read': False, 'YOLO_rerun': False,
    'fixed_threshold_transfer_performed': False, 'cases': cases,
}, ensure_ascii=False, indent=2)+'\n')
(OUT/'research_input_hashes.json').write_text(json.dumps(INPUTS, ensure_ascii=False, indent=2)+'\n')
(OUT/'research_checks.json').write_text(json.dumps({'passed': all(c['passed'] for c in CHECKS),
    'check_count': len(CHECKS), 'checks': CHECKS}, ensure_ascii=False, indent=2)+'\n')
print(json.dumps({'passed': True, 'checks': len(CHECKS), 'small_inputs_hashed': len(INPUTS)}))
