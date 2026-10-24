#!/usr/bin/env python3
"""Verify existing five-camera deliverables and adapt them for Unity, without re-analysis.

The source directories are read-only inputs. Video is referenced in place. The adapter
preserves every observation, threshold, null and classification; it only normalizes
geometry/labels and adds explicit availability flags for Unity JsonUtility.
"""
import argparse
import bisect
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

PROJECT = Path(__file__).resolve().parents[1]
OUTPUTS = PROJECT.parent / 'traffic_yolo_system' / 'outputs'
STATE_DIR = OUTPUTS / 'five_intersection_states_2026-09-28_v1'
VIDEO_INDEX = OUTPUTS / 'five_intersections_2026-09-27_r2' / 'five_video_results_index.json'
OUT = PROJECT / 'Assets' / 'StreamingAssets' / 'five_sources'
VERIFY = PROJECT / 'Verification' / 'five_sources'
FFPROBE = PROJECT.parent / 'traffic_yolo_system' / 'tools' / 'full_video' / 'ffprobe_7_1_1_arm64' / 'ffprobe'
CAMERAS = [('Newport', 4), ('NE12th', 5), ('Eastgate', 5), ('SE38th', 4), ('NE8th', 4)]
LABELS = {'road_left': 'Left road', 'road_upper_left': 'Upper-left road', 'road_top': 'Upper road',
          'road_right': 'Right road', 'road_bottom': 'Lower road', 'junction_center': 'Junction center'}
COLORS = {'road_left': '#FFD700', 'road_upper_left': '#FF8C42', 'road_top': '#5ADC50',
          'road_right': '#4196FF', 'road_bottom': '#37B6B6', 'junction_center': '#D25AE6'}
NOTES = [
    'Offline 10-second observations become available only after their window ends; this is not a real-time detector.',
    'Relative count levels compare one image region within its own source hour. Different sources must not be ranked as real congestion.',
    'Image motion is measured in source-image pixels/second, not road speed or km/h; relatively low motion does not mean stopped.',
    'Persistent high count and relatively low motion can include normal signal waiting. No congestion labels have been validated against ground truth.',
    'No observed vehicles does not prove an empty road. No persistent signal does not establish free flow.',
    'Any long-held frame invalidates the affected 10-second state window. Incomplete and missing-evidence windows remain unclassified.',
    'Unavailable measurements remain JSON null and are displayed as insufficient evidence, never substituted with zero.',
    'ROI positions are source-image coordinates. The exported video adds a 150-pixel information bar above the 1280 x 720 source image.',
    'Validation-only offline development data. Camera geometry, perspective, occlusion, night glare and tracking errors limit interpretation.'
]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write(path, value, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, allow_nan=False,
                      indent=None if compact else 2, separators=(',', ':') if compact else None)
    path.write_text(text + '\n', encoding='utf-8')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def probe_and_verify(camera, data, index_entry, ffprobe, input_manifest):
    src = data['source']
    video = Path(index_entry['video'])
    require(str(video) == src['video_path'], f'{camera}: state/index video disagreement')
    require(index_entry['frames'] == src['frame_count'], f'{camera}: frame-count disagreement')
    video_hash = digest(video)
    require(video_hash == src['video_sha256'], f'{camera}: video hash mismatch')
    command = [str(ffprobe), '-v', 'error', '-select_streams', 'v:0', '-show_entries',
               'stream=codec_name,width,height,time_base,duration,start_pts,nb_frames:packet=pts,duration',
               '-of', 'json', str(video)]
    proc = subprocess.run(command, check=True, capture_output=True, text=True)
    require(not proc.stderr.strip(), f'{camera}: ffprobe reported errors: {proc.stderr}')
    result = json.loads(proc.stdout)
    stream = result['streams'][0]
    require((stream['width'], stream['height']) == (src['video_width'], src['video_height']) == (1280, 870),
            f'{camera}: unexpected exported dimensions')
    require((src['width'], src['height'], src['content_x'], src['content_y'], src['content_width'], src['content_height'])
            == (1280, 720, 0, 150, 1280, 720), f'{camera}: invalid source content rectangle')
    num, den = map(int, stream['time_base'].split('/'))
    require((num, den) == (src['time_base']['num'], src['time_base']['den']), f'{camera}: time-base mismatch')
    pts = sorted(int(x['pts']) for x in result['packets'])
    require(len(pts) == src['frame_count'] == int(stream['nb_frames']), f'{camera}: exported sample count mismatch')
    require(pts[0] == 0 and all(a < b for a, b in zip(pts, pts[1:])), f'{camera}: invalid presentation ordering')
    require(abs(float(stream['duration']) - src['duration_s']) < 0.00001, f'{camera}: duration mismatch')
    if camera == 'Newport':
        timing = OUTPUTS / 'newport_full_v5_2026-09-24' / 'downloaded_server_results' / 'time_audit_v2' / 'timing_frames.jsonl'
    else:
        timing = Path(data['source_paths']['pts_audit']).with_name('timing_frames.jsonl')
    require(str(timing) in input_manifest, f'{camera}: audited timing is absent from state input manifest')
    require(digest(timing) == input_manifest[str(timing)]['sha256'], f'{camera}: timing audit hash mismatch')
    with timing.open(encoding='utf-8') as f:
        audited = [json.loads(line) for line in f if line.strip()]
    require(len(audited) == len(pts), f'{camera}: timing audit count mismatch')
    require(all(int(row['pts']) == tick for row, tick in zip(audited, pts)), f'{camera}: exported PTS differ from original audit')
    require(all((row['width'], row['height']) == (1280, 720) for row in audited), f'{camera}: audited source dimensions mismatch')
    seconds = [tick * num / den for tick in pts]
    if 'last_frame_time_s' in src:
        require(abs(seconds[-1] - src['last_frame_time_s']) < 1e-8, f'{camera}: last-frame PTS mismatch')
    holds = [i for i in range(len(pts) - 1) if (pts[i + 1] - pts[i]) * num / den > 0.5]
    require(holds == [h['frame_index'] for h in data['held_intervals']], f'{camera}: held-frame indices mismatch')
    for h in data['held_intervals']:
        require(pts[h['frame_index']] == h['start_ticks'] and pts[h['frame_index'] + 1] == h['end_ticks'],
                f'{camera}: held interval PTS mismatch')
    return seconds, {'camera': camera, 'video_path': str(video), 'video_sha256': video_hash,
                     'video_bytes': video.stat().st_size, 'ffprobe_command': command,
                     'stream': stream, 'packet_count': len(pts), 'first_pts': pts[0], 'last_pts': pts[-1],
                     'audited_timing_path': str(timing), 'audited_timing_sha256': digest(timing),
                     'all_exported_pts_equal_original_audit': True, 'long_hold_count': len(holds),
                     'coordinate_rectangle_verified': True,
                     'probe_scope': 'All MP4 video sample PTS; no claim of full pixel decode or Unity playback validation.'}


def adapt(camera, original, rules, seconds):
    data = copy.deepcopy(original)
    data['schema_version'] = 'five_intersection_demo_v1'
    src = data['source']
    src['last_frame_time_s'] = seconds[-1]
    src['frame_pts_s'] = seconds
    src['night_case'] = bool(original['night_case'])
    src['limitation'] = ('Night glare: all five regions lack reliable count-level and relative-motion calibration. '
                         'Retain count observations; road condition is unknown.' if src['night_case'] else
                         'Model estimates within this source and image region only; no measured road density, speed or validated congestion class.')
    src['state_path'] = str(STATE_DIR / camera / 'states_10s.json')
    src['rules_path'] = str(STATE_DIR / camera / 'rules_and_thresholds.json')
    src['report_path'] = str(STATE_DIR / camera / '中文结果说明.md')
    src['state_sha256'] = digest(Path(src['state_path']))
    src['rules_sha256'] = digest(Path(src['rules_path']))
    data['notes'] = original.get('notes', NOTES)
    for zone in data['zones']:
        threshold = rules['thresholds'][zone['id']]
        zone.setdefault('label', LABELS[zone['id']])
        zone.setdefault('color_hex', COLORS[zone['id']])
        if 'polygon' not in zone:
            zone['polygon'] = [{'x': x / src['width'], 'y': y / src['height']} for x, y in zone['polygon_points']]
        zone['density_low_upper'] = threshold['count_p33']
        zone['density_high_lower'] = threshold['count_p67']
        zone['motion_low_upper'] = threshold['motion_low_upper']
        zone['density_calibration_windows'] = threshold['count_calibration_windows']
        zone['motion_calibration_windows'] = threshold['motion_calibration_windows']
        zone['count_thresholds_available'] = threshold['count_p33'] is not None and threshold['count_p67'] is not None
        zone['count_calibration_available'] = zone['count_thresholds_available'] and threshold['count_p33'] < threshold['count_p67']
        zone['motion_calibration_available'] = threshold['motion_low_upper'] is not None
    for window in data['windows']:
        for zone in window['zones']:
            zone['count_available'] = zone['count_mean'] is not None
            zone['motion_low_relative_available'] = zone.get('motion_low_relative') is not None
            zone['density_level'] = zone['relative_count_level']
            zone['density_label'] = zone['relative_count_label']
            zone['traffic_state'] = zone['state_hint']
            zone['traffic_state_label'] = zone['state_label']
    milestones = [{'time_s': 0.0, 'label': 'Start: collect the first 10-second window'}]
    for show in read(STATE_DIR / camera / 'showcase_intervals.json'):
        if 'confirmation_available_at_s' in show:
            t = show['confirmation_available_at_s']
            label = LABELS[show['zone']] + ': confirmed ' + ('high count + relatively low motion' if show['kind'] == 'joint' else 'persistent high count')
        elif show['kind'] == 'count_peak':
            t = show['end_s']
            label = 'Count-peak window has ended (model observations)'
        else:
            t = show['start_s']
            label = 'Held image: evidence insufficient'
        if t < src['duration_s']:
            milestones.append({'time_s': t, 'label': label})
    data['milestones'] = milestones
    return data


def test_adaptation(camera, original, adapted, rules):
    require(len(adapted['zones']) == dict(CAMERAS)[camera], f'{camera}: zone count mismatch')
    immutable_fields = ('start_s', 'end_s', 'complete', 'full_valid_window', 'held_duration_s')
    zone_fields = tuple(original['windows'][0]['zones'][0].keys())
    nulls = 0
    for a, b in zip(original['windows'], adapted['windows']):
        for key in immutable_fields:
            require(a[key] == b[key], f'{camera}: changed {key}')
        for za, zb in zip(a['zones'], b['zones']):
            for key in zone_fields:
                require(za.get(key) == zb.get(key), f'{camera}: changed observation {key}')
                nulls += za.get(key) is None
            require(zb['count_available'] == (zb['count_mean'] is not None), 'count availability mismatch')
            require(zb['motion_available'] == (zb['motion_median_px_s'] is not None), 'motion availability mismatch')
    for zone in adapted['zones']:
        t = rules['thresholds'][zone['id']]
        require(zone['density_low_upper'] == t['count_p33'] and zone['density_high_lower'] == t['count_p67']
                and zone['motion_low_upper'] == t['motion_low_upper'], 'threshold mutation')
        require(all(0 <= p['x'] <= 1 and 0 <= p['y'] <= 1 for p in zone['polygon']), 'invalid normalized polygon')
    starts = [w['start_s'] for w in adapted['windows']]
    def at(t):
        i = bisect.bisect_right(starts, t) - 1
        return i if 0 <= i < len(starts) and t < adapted['windows'][i]['end_s'] else -1
    def eligible(w):
        return w['complete'] and w['full_valid_window'] and w['held_duration_s'] == 0
    def confirmed(t):
        i = at(t)
        if i <= 0 or not eligible(adapted['windows'][i]):
            return -1
        prev = adapted['windows'][i - 1]
        return i - 1 if eligible(prev) and prev['end_s'] <= t else -1
    checks = 0
    for i, w in enumerate(adapted['windows']):
        require(at(w['start_s']) == i, 'half-open start mismatch')
        require(at(w['end_s']) != i, 'half-open end mismatch')
        require(confirmed(w['start_s'] + (w['end_s'] - w['start_s']) / 2) <= i - 1, 'future state leaked')
        if not eligible(w):
            require(confirmed(w['start_s']) == -1, 'stale state in invalid window')
        checks += 4
    require(confirmed(0) == -1 and confirmed(adapted['source']['duration_s']) == -1, 'start/EOF mismatch')
    if camera == 'NE12th':
        require(all(not z['count_calibration_available'] and not z['motion_calibration_available'] for z in adapted['zones']), 'night calibration overclaim')
        require(not any(z['relative_count_level'] in ('low', 'medium', 'high') for w in adapted['windows'] for z in w['zones']), 'night grading overclaim')
    return {'camera': camera, 'zone_count': len(adapted['zones']), 'window_count': len(starts),
            'all_source_observations_and_thresholds_unchanged': True, 'null_observation_fields_preserved': nulls,
            'half_open_confirmation_checks': checks + 2, 'passed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffprobe', type=Path, default=FFPROBE)
    args = parser.parse_args()
    final = read(STATE_DIR / 'final_state_verification.json')
    require(final['completed'], 'State derivation is incomplete')
    originals = {name: read(STATE_DIR / name / 'states_10s.json') for name, _ in CAMERAS}
    before = {}
    for name, expected in final['files'].items():
        actual = digest(STATE_DIR / name)
        require(actual == expected, f'State manifest mismatch: {name}')
        before[name] = actual
    require(len(before) == 60, 'Unexpected state manifest file count')
    index = read(VIDEO_INDEX)
    require(index['completed'] and index['count'] == 5, 'Incomplete video index')
    indexed = {item['intersection']: item for item in index['results']}
    input_manifest = read(STATE_DIR / 'input_manifest.json')
    report = {'schema': 'five-source-unity-input-verification-v1', 'started_utc': datetime.now(timezone.utc).isoformat(),
              'manifest_file_count': len(before), 'state_manifest_hashes_before': before,
              'final_state_verification_sha256': digest(STATE_DIR / 'final_state_verification.json'),
              'video_index_sha256': digest(VIDEO_INDEX), 'source_manifest': str(STATE_DIR / 'final_state_verification.json'),
              'videos': [], 'adaptation_tests': [], 'video_copy_count': 0}
    with ThreadPoolExecutor(max_workers=3) as pool:
        probes = {name: pool.submit(probe_and_verify, name, originals[name], indexed[name], args.ffprobe, input_manifest)
                  for name, _ in CAMERAS}
        catalog = {'schema_version': 'five_source_catalog_v1', 'entries': []}
        for name, _ in CAMERAS:
            seconds, verification = probes[name].result()
            original = originals[name]
            rules = read(STATE_DIR / name / 'rules_and_thresholds.json')
            adapted = adapt(name, original, rules, seconds)
            report['adaptation_tests'].append(test_adaptation(name, original, adapted, rules))
            report['videos'].append(verification)
            data_path = name + '/traffic_demo.json'
            write(OUT / data_path, adapted, compact=True)
            catalog['entries'].append({'id': original['source']['id'], 'label': name, 'data_path': 'five_sources/' + data_path,
                                       'data_sha256': digest(OUT / data_path), 'source_state_path': adapted['source']['state_path'],
                                       'source_state_sha256': adapted['source']['state_sha256'],
                                       'source_rules_path': adapted['source']['rules_path'],
                                       'source_rules_sha256': adapted['source']['rules_sha256'],
                                       'video_path': adapted['source']['video_path'], 'video_sha256': adapted['source']['video_sha256']})
            print(f'{name}: video hash, {len(seconds):,} PTS, geometry, nulls and observations verified', flush=True)
    write(OUT / 'catalog.json', catalog)
    report['state_manifest_hashes_after'] = {name: digest(STATE_DIR / name) for name in before}
    require(report['state_manifest_hashes_after'] == before, 'Read-only source content changed during import')
    report['source_manifest_unchanged'] = True
    report['catalog_sha256'] = digest(OUT / 'catalog.json')
    report['completed_utc'] = datetime.now(timezone.utc).isoformat()
    report['passed'] = True
    write(VERIFY / 'input_verification.json', report)
    print('All 60 state-manifest files verified before and after; five adapted sources written.', flush=True)


if __name__ == '__main__':
    main()
