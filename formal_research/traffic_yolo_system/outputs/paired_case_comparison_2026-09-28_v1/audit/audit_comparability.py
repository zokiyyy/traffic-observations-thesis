#!/usr/bin/env python3
"""Read-only audit of 10 completed Train/Validation video runs; no inference.

Run with Python 3 from any directory. Outputs only beside this script.
Original videos are read as bytes only to verify their frozen source SHA256.
No Main Test images/labels, model inference, external service or source edits.
"""
import csv
import datetime as dt
import difflib
import hashlib
import json
import tarfile
from pathlib import Path

OUT = Path(__file__).resolve().parent
BASE = OUT.parents[1]
PROJECT = BASE.parents[2]
OLD = BASE / 'five_intersections_2026-09-27_r2'
NEW = BASE / 'supplementary_five_2026-09-28_v3_repair'
NP = BASE / 'newport_full_v5_2026-09-24'
CAMERAS = ['Bellevue_150th_Newport', 'Bellevue_116th_NE12th',
           'Bellevue_150th_Eastgate', 'Bellevue_150th_SE38th', 'Bellevue_Bellevue_NE8th']
INPUTS = {}
CHECKS = []

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def record(path):
    path = path.resolve()
    if str(path) not in INPUTS:
        INPUTS[str(path)] = {'path': str(path), 'bytes': path.stat().st_size,
                            'sha256': digest(path)}
    return INPUTS[str(path)]

def read(path):
    record(path)
    return json.loads(path.read_text())

def check(name, passed, details=None):
    CHECKS.append({'check': name, 'passed': bool(passed), 'details': details})

def geometry(roi):
    return {**{k: roi.get(k) for k in ['frame_width', 'frame_height', 'assignment_point',
                                      'boundary_tie_break']},
            'zones_in_assignment_order': [{'zone_name': z['zone_name'],
                                           'polygon_points': z['polygon_points']}
                                          for z in roi['zones']]}

def semantic_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def background_values(bg):
    if bg['mode'] == 'none':
        return {'mode': 'none'}
    policy = bg['policy']
    return {'mode': bg['mode'], 'confirmation_imgsz': bg['confirmation_imgsz'],
            **{k: policy[k] for k in ['frame_width', 'frame_height',
                    'reviewed_background_polygon_xy', 'confirmation_iou', 'confirm_min_confidence',
                    'model_sha256']}}

def code_hashes(p):
    return {k.split('/src/')[-1]: v for k, v in p['implementation_hashes'].items()}

selection = {tag: {e['camera_id']: e for e in read(root/'selection/selected_sources.json')['entries']}
             for tag, root in [('original', OLD), ('supplementary', NEW)]}
for root, idx in [(OLD, 'five_video_results_index.json'), (NEW, 'supplementary_results_index.json')]:
    read(root/idx)
    final = read(root/'final_delivery_verification.json')
    check(f'{root.name}: delivery completed', final['completed'] and final['local_verified_count'] == 5)

manifest_path = PROJECT / 'formal_research/03_full_dataset_design/splits/formal_source_video_split_manifest_v1_2026-08-24.csv'
manifest_sha = record(manifest_path)['sha256']
with manifest_path.open() as stream:
    manifest = {r['Source Video Filename']: r for r in csv.DictReader(stream)}

pairs = []
for cam in CAMERAS:
    pair = {'camera_id': cam}
    internals = {}
    for tag in ['original', 'supplementary']:
        new = tag == 'supplementary'
        np_original = not new and cam.endswith('Newport')
        root = NEW if new else OLD
        cfg = NP/'config' if np_original else root/'config'/cam
        if np_original:
            provenance_path = next((NP/'downloaded_server_results/full_runs').rglob('provenance.json'))
            timing_root = NP/'downloaded_server_results/time_audit_v2'
            qc_path = NP/'downloaded_server_results/analysis/server_results/qc_summary.json'
            applicability_path = cfg/'AI稀疏构图核查.json'
        else:
            archive = root/'delivery'/cam/('full_data_archive' if new else 'data_archive')
            provenance_path = archive/'attempt-000001/provenance.json'
            timing_root = archive/'output'
            qc_path = root/'delivery'/cam/'summary_archive/output/qc_summary.json'
            applicability_path = cfg/'applicability.json'
        p = read(provenance_path)
        implementation_snapshot_files = []
        if np_original:
            archive_path = NP/'remote_runtime_v5.tar.gz'
            record(archive_path)
            with tarfile.open(archive_path) as archive:
                for name, expected in code_hashes(p).items():
                    member = next(m for m in archive.getmembers() if m.name.endswith('/src/'+name))
                    actual = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
                    check(f'{tag}/{cam}: implementation snapshot {name}', actual == expected)
                    implementation_snapshot_files.append({'archive': str(archive_path), 'member': member.name,
                                                           'sha256_recomputed': actual})
        else:
            code_root = NEW/'code/src' if new else (
                OLD/'repairs/eastgate_frame_count_20260927_v2/code/src' if cam.endswith('Eastgate') else BASE.parent/'src')
            for name, expected in code_hashes(p).items():
                snapshot = code_root/name
                actual = record(snapshot)['sha256']
                check(f'{tag}/{cam}: implementation snapshot {name}', actual == expected)
                implementation_snapshot_files.append({'path': str(snapshot), 'sha256_recomputed': actual})
        roi = read(cfg/'roi.json')
        q = read(qc_path)
        pts = read(timing_root/'pts_summary.json')
        decoder = read(timing_root/'decoder_summary.json')
        applicability = read(applicability_path)
        for name in ['camera.json', 'camera_registry.json', 'full_remote.json']:
            read(cfg/name)
        timing_path = timing_root/'timing_frames.jsonl'
        timing_sha = record(timing_path)['sha256']
        entry = selection[tag][cam]
        source = Path(entry['source_path'])
        source_sha = record(source)['sha256']
        sid = p['task']['source_id']
        split = p['task']['split']
        m = manifest[sid]
        c = p['config']
        check(f'{tag}/{cam}: source identity and SHA',
              sid == entry['source_id'] == q['source_id'] and
              source_sha == c['input']['sha256'] == entry['source_sha256'] == q['source_sha256'])
        check(f'{tag}/{cam}: split identity', split == entry['split'] == m['Formal Split'])
        check(f'{tag}/{cam}: frozen split manifest SHA', manifest_sha == c['split_manifest_sha256'])
        check(f'{tag}/{cam}: ROI bound to recorded run', record(cfg/'roi.json')['sha256'] in p['input_hashes'].values())
        check(f'{tag}/{cam}: timing SHA bound to QC', timing_sha == q['pts_sha256'])
        check(f'{tag}/{cam}: model bound to QC', c['model']['sha256'] == q['model_sha256'])
        check(f'{tag}/{cam}: frame counts agree',
              pts['frame_count'] == q['totals']['frames'] == p['preflight']['frame_count'] == decoder['decoded_frames'])
        check(f'{tag}/{cam}: QC passed without missing output', q['status'] == 'passed' and
              q['error_count'] == 0 and q['missing_duration_seconds'] == 0)
        check(f'{tag}/{cam}: decoder and PTS integrity', decoder['all_frames_match'] and decoder['eof_verified'] and
              pts['continuous_presentation_coverage'] and pts['duplicate_or_backwards_pts_count'] == 0 and
              pts['missing_nonpositive_duration_count'] == 0 and pts['ffprobe_error_count'] == 0)
        tick = 1 / 90000
        duration = pts['normalized_presented_duration_seconds']
        hist = {int(k): int(v) for k, v in pts['frame_duration_ticks_histogram'].items()}
        long = {k: v for k, v in hist.items() if k*tick > .5}
        hold_count = sum(long.values())
        hold_seconds = sum(k*v*tick for k, v in long.items())
        sampling = q.get('source_image_observation_sampling')
        if sampling:
            check(f'{tag}/{cam}: holds agree with QC', hold_count == sampling['long_presentation_intervals'] and
                  abs(hold_seconds - sampling['long_presentation_interval_duration_seconds']) < 1e-8)
        check(f'{tag}/{cam}: durations agree', abs(duration - q['valid_duration_seconds']) < 1e-8)
        bg = p['camera']['background_filter']
        if bg['mode'] != 'none':
            bpath = cfg/'background_policy.json'
            policy = read(bpath)
            check(f'{tag}/{cam}: background policy bound to run', record(bpath)['sha256'] == bg['policy_sha256'] == q['background_policy_sha256'])
            if new:
                read(cfg/'background_applicability.json')
        geom = geometry(roi)
        check(f'{tag}/{cam}: all source images match ROI dimensions',
              pts['dimensions_histogram'] == {f"{geom['frame_width']}x{geom['frame_height']}": pts['frame_count']})
        info = {
            'source_id': sid, 'source_split': split, 'source_original_path': str(source),
            'source_sha256_recomputed': source_sha, 'lighting_metadata': m['Lighting'],
            'duration_seconds_original_pts': duration, 'frame_count': pts['frame_count'],
            'source_frame_dimensions': [geom['frame_width'], geom['frame_height']],
            'first_pts_ticks': pts['first_pts_ticks'], 'time_base': pts['time_base'],
            'model': c['model'], 'detector': c['detector'], 'tracker': c['tracker'],
            'configured_device': c['device'],
            'dedup': {k: v for k, v in c['dedup'].items() if k != 'containment_policy_path'},
            'aggregation': c['aggregation'], 'time_mode': c['time']['mode'],
            'max_allowed_frame_duration_seconds': c['time']['max_frame_duration_seconds'],
            'runtime': p['runtime'], 'roi_layout_version': roi['layout_version'],
            'audited_decoder': p['preflight']['audited_decoder'],
            'decoder_timestamp_tolerance_seconds': p['preflight']['decoder_timestamp_tolerance_seconds'],
            'roi_geometry_and_assignment': geom, 'roi_geometry_semantic_sha256': semantic_sha(geom),
            'background_filter_numeric_policy': background_values(bg),
            'background_policy_file_sha256': bg.get('policy_sha256'),
            'background_candidate_frame_observations': q['totals'].get('background_candidates', 0),
            'background_removed_frame_observations': q['totals'].get('background_removed', 0),
            'applicability_document': applicability,
            'qc_status': q['status'], 'qc_error_count': q['error_count'],
            'missing_output_duration_seconds': q['missing_duration_seconds'],
            'long_hold_definition': 'One original displayed-frame interval > 0.5 seconds; not a new observation throughout',
            'long_hold_count_recomputed_from_pts_histogram': hold_count,
            'long_hold_duration_seconds_recomputed': hold_seconds,
            'long_hold_fraction': hold_seconds/duration,
            'maximum_frame_duration_seconds': max(hist)*tick,
            'code_hashes': code_hashes(p),
            'implementation_snapshot_files_rehashed': implementation_snapshot_files,
            'container_frame_count': p['preflight'].get('container_declared_frame_count', decoder['reported_frame_count']),
            'audited_frame_count': p['preflight']['frame_count'],
            'evidence_paths': {'provenance': str(provenance_path), 'roi': str(cfg/'roi.json'),
                               'qc': str(qc_path), 'pts_summary': str(timing_root/'pts_summary.json'),
                               'timing_frames': str(timing_path), 'applicability': str(applicability_path)},
        }
        pair[tag] = info
        internals[tag] = p
    o, n = pair['original'], pair['supplementary']
    fields = ['model', 'detector', 'tracker', 'dedup', 'aggregation', 'time_mode', 'configured_device',
              'audited_decoder', 'decoder_timestamp_tolerance_seconds', 'time_base',
              'max_allowed_frame_duration_seconds', 'runtime', 'roi_geometry_and_assignment',
              'background_filter_numeric_policy']
    comparison = {field: o[field] == n[field] for field in fields}
    for field, same in comparison.items():
        check(f'{cam}: paired {field} equivalence', same)
    oc, nc = o['code_hashes'], n['code_hashes']
    comparison['implementation_differences'] = [
        {'file': k, 'original_sha256': oc.get(k), 'supplementary_sha256': nc.get(k)}
        for k in sorted(set(oc) | set(nc)) if oc.get(k) != nc.get(k)]
    comparison['source_id_distinct'] = o['source_id'] != n['source_id']
    a, z = [dt.datetime.strptime(pair[t]['source_id'].split('__')[1], '%Y-%m-%d_%H-%M-%S.mp4')
            for t in ['original', 'supplementary']]
    overlap = max(0., (min(a+dt.timedelta(seconds=o['duration_seconds_original_pts']),
                          z+dt.timedelta(seconds=n['duration_seconds_original_pts'])) - max(a, z)).total_seconds())
    comparison['filename_time_overlap_seconds'] = overlap
    comparison['filename_time_warning'] = 'Filename timestamps used as unzoned labels only; not independently calibrated wall-clock time.'
    check(f'{cam}: source interval labels nonoverlapping', overlap == 0)
    pair['comparison'] = comparison
    pairs.append(pair)

# Read exact archived Newport implementation bytes without extracting or running.
runtime_archive = NP/'remote_runtime_v5.tar.gz'
record(runtime_archive)
with tarfile.open(runtime_archive) as archive:
    member = next(m for m in archive.getmembers() if m.name.endswith('/full_video/video.py'))
    old_bytes = archive.extractfile(member).read()
    check('Newport archived implementation matches run hash', hashlib.sha256(old_bytes).hexdigest() ==
          pairs[0]['original']['code_hashes']['full_video/video.py'])
new_video = NEW/'code/src/full_video/video.py'
record(new_video)
new_pts = NEW/'code/src/full_video/pts_timing.py'
old_pts = BASE.parent/'src/full_video/pts_timing.py'
record(new_pts)
record(old_pts)
check('Original pts_timing local snapshot matches original run', record(old_pts)['sha256'] ==
      pairs[0]['original']['code_hashes']['full_video/pts_timing.py'])
diffs = {
    'newport_video_py.diff': ''.join(difflib.unified_diff(old_bytes.decode().splitlines(True),
        new_video.read_text().splitlines(True), fromfile='original-Newport-recorded-video.py', tofile='supplementary-video.py')),
    'pts_timing_py.diff': ''.join(difflib.unified_diff(old_pts.read_text().splitlines(True),
        new_pts.read_text().splitlines(True), fromfile='original-recorded-pts_timing.py', tofile='supplementary-pts_timing.py')),
}
for filename, contents in diffs.items():
    (OUT/filename).write_text(contents)
read(OLD/'repairs/eastgate_frame_count_20260927_v2/repair_manifest.json')
record(Path(__file__))
result = {
    'schema_version': 'paired-case-comparability-audit-v1',
    'generated_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
    'scope': 'Read-only completed original vs supplementary five sources; configuration and provenance equivalence, not ground truth accuracy',
    'main_test_images_or_labels_read': False, 'inference_run': False,
    'frozen_split_manifest_sha256': manifest_sha, 'pairs': pairs,
    'implementation_difference_interpretation': {
        'pts_timing.py': 'Container nb_frames becomes retained diagnostic; full FFprobe/OpenCV EOF, timing, decode and coverage remain authoritative. No PTS/frame insertion or deletion. Old Eastgate already used this repair.',
        'video.py': 'Only changed decoded-frame hashing from background-enabled sources to all sources. Newport has background enabled in both runs, so its hashing branch is equivalent. Inference/filters unchanged.',
    },
    'interpretation_limits': [
        'Equal declared ROI coordinates and numeric parameters do not establish equal real-world measurement error or all-frame geometric alignment.',
        'Each source is one observation period. Changes cannot be attributed solely to time; lighting, traffic composition, occlusion and sampling differ.',
        'NE12th supplementary is 408.577-second Train data versus 3600.010-second night Validation; not equal-length independent validation.',
        'Newport background geometry/parameters are unchanged but applicability evidence is source-specific sparse review; numeric equivalence does not establish removal correctness.',
        'Recorded missing output is zero, but held-image intervals provide no new visual observations and must remain unknown in state/motion inference.',
        'Per-source P33/P67/P25 state thresholds are separately calibrated relative references; High/Mid/Low labels cannot be directly compared between sources.',
        'QC proves engineering consistency only. Current count is not cumulative flow, real density, real congestion or count accuracy.',
    ],
}
(OUT/'comparability_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
(OUT/'audit_input_hashes.json').write_text(json.dumps({'files': list(INPUTS.values())}, ensure_ascii=False, indent=2)+'\n')
verification = {'passed': all(c['passed'] for c in CHECKS), 'check_count': len(CHECKS),
                'failed_count': sum(not c['passed'] for c in CHECKS), 'checks': CHECKS}
(OUT/'audit_checks.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2)+'\n')
print(json.dumps({k: v for k, v in verification.items() if k != 'checks'}, ensure_ascii=False))
if not verification['passed']:
    print(json.dumps([c for c in CHECKS if not c['passed']], ensure_ascii=False, indent=2))
    raise SystemExit(1)
