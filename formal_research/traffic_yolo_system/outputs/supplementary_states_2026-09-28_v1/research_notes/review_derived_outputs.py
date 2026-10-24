#!/usr/bin/env python3
"""Independent review of saved states and display semantics; small JSON only."""
import hashlib
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HASHES = {}
checks = 0

def read(p):
    b = p.read_bytes()
    assert len(b) < 8_000_000
    HASHES[str(p)] = hashlib.sha256(b).hexdigest()
    return json.loads(b)

def ok(x):
    global checks
    checks += 1
    assert x

probe = read(HERE/'calibration_scope_probe.json')
summary = read(ROOT/'region_summary.json')
indexed = read(ROOT/'results_index.json')
ok(indexed['completed'] and not indexed['transfer_experiment_performed'])
probe_by_case = {p['case']: p for p in probe['cases']}
case_totals = []
for case in ['Newport', 'NE12th', 'Eastgate', 'SE38th', 'NE8th']:
    d = read(ROOT/case/'states_10s.json')
    pr = probe_by_case[case]
    ok(d['source'] == pr['source'])
    expected = {r['zone_id']: r for r in pr['zones']}
    for zone, threshold in d['thresholds'].items():
        p = expected[zone]
        for outkey, pk in [('count_p33','count_p33'), ('count_p67','count_p67'),
                           ('motion_low_upper','motion_p25_px_s')]:
            a, b = threshold[outkey], p[pk]
            ok(a is None and b is None or a is not None and b is not None and abs(a-b)<1e-10)
        ok(threshold['motion_calibration_windows'] == p['motion_calibration_windows'])
        ok(threshold['threshold_source_id'] == d['source']['id'])
        ok(threshold['threshold_source_split'] == d['source']['split'])
    streaks = {z: [0.,0.] for z in d['thresholds']}
    sums = {z: Counter() for z in d['thresholds']}
    prev_end = None
    for w in d['windows']:
        ok(w['confirmation_source_time_s'] == w['end_s'])
        seconds = w['end_s'] - w['start_s']
        for r in w['zones']:
            z = r['zone_id']; t = d['thresholds'][z]
            n = r['count_mean']
            if not w['complete']:
                level = 'unknown'
            elif n == 0:
                level = 'empty'
            elif t['count_p67'] <= t['count_p33']:
                level = 'unknown'
            else:
                level = 'low' if n <= t['count_p33'] else 'medium' if n <= t['count_p67'] else 'high'
            ok(r['relative_count_level'] == level)
            old = streaks[z] if prev_end is None or abs(w['start_s']-prev_end)<1e-9 else [0.,0.]
            high = w['complete'] and level == 'high'
            motion_ready = r['motion_available'] and t['motion_low_upper'] is not None
            joint = high and motion_ready and r['motion_median_px_s'] <= t['motion_low_upper']
            streaks[z] = [old[0]+seconds if high else 0., old[1]+seconds if joint else 0.]
            ok(abs(r['high_count_duration_s']-streaks[z][0]) < 1e-9)
            ok(abs(r['joint_duration_s']-streaks[z][1]) < 1e-9)
            high_hint, joint_hint = streaks[z][0] >= 30, streaks[z][1] >= 30
            ok(high_hint == r['high_count_persistent_count_only'])
            ok(joint_hint == r['joint_hint'])
            ok(not joint_hint or high_hint)
            unknown = not w['complete'] or t['count_p67'] <= t['count_p33'] or not motion_ready
            expected_state = 'unknown' if unknown else 'joint_hint' if joint_hint else 'high_count_hint' if high_hint else 'no_persistent_hint'
            ok(r['evidence_state'] == expected_state)
            ok(bool(r['evidence_unknown_reasons']) == unknown)
            sums[z]['rows'] += 1
            sums[z]['high'] += high_hint
            sums[z]['joint'] += joint_hint
            sums[z]['unknown'] += unknown
            sums[z]['high_and_unknown'] += high_hint and unknown
        prev_end = w['end_s']
    for z, vals in sums.items():
        sr = next(s for s in summary if s['case'] == case and s['zone_id'] == z)
        for key, skey in [('rows','all_windows'), ('high','high_count_hint_windows'),
                          ('joint','joint_hint_windows'), ('unknown','evidence_unknown_windows'),
                          ('high_and_unknown','high_count_hints_with_unknown_evidence')]:
            ok(vals[key] == sr[skey])
    total = {'case': case, 'split': d['source']['split'], **{key: sum(s[key] for s in sums.values()) for key in ['rows','high','joint','unknown','high_and_unknown']}}
    case_totals.append(total)
    ok(case != 'NE8th' or total['unknown'] == total['rows'] and total['high'] == 0 and total['joint'] == 0)
    ok(case != 'NE12th' or d['source']['split'] == 'Train')

total = {key: sum(c[key] for c in case_totals) for key in ['rows','high','joint','unknown','high_and_unknown']}
result = {'passed': True, 'check_count': checks, 'scope': 'Saved small-output independent thresholds, recurrence, subset counts and UI evidence gating review',
          'case_totals': case_totals, 'totals': total,
          'unit': 'region x 10-second window; contains short unclassified tail rows',
          'joint_is_subset_of_high_count': True,
          'high_and_unknown_retained_separately': True,
          'traffic_accuracy_evaluated': False,
          'full_video_or_frame_logs_read': False, 'input_sha256': HASHES,
          'review_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
(HERE/'derived_output_research_review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
print(json.dumps({'passed': True, 'checks': checks, 'totals': total}, ensure_ascii=False))
