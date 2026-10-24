#!/usr/bin/env python3
"""Fixed four-source server job; Newport is metadata-only reuse, never inference.

Only --expected-job-sha256 and --validate-only are accepted. Stage completion is
content-addressed and immutable. Failed/interrupted inference restarts the whole
configured interval with new tracker state; no frame-level checkpoint is claimed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import socket
import subprocess
import sys
import tarfile
import time
import traceback
import uuid

REMOTE_ROOT = Path('/home/ubuntu/traffic_projects/five_intersections_20260927_r2')
SYSTEM_REL = Path('formal_research/traffic_yolo_system')
NEW_REL = SYSTEM_REL / 'outputs/five_intersections_2026-09-27_r2'
PYTHON = '/home/ubuntu/venvs/traffic-yolo/bin/python'
MODEL_PATH = '/home/ubuntu/traffic_projects/runs/training/yolo11s_formal_a100_v1/weights/best.pt'
PYAV_ROOT = '/home/ubuntu/traffic_projects/full_v5_20260924/tools/pyav17'
MODEL_SHA = '07e2280eda613c5d633196729f8716e9715000430171f49f6341afe573a2f334'
ALLOWED = {
    'Bellevue_150th_Eastgate__2017-09-11_17-08-33.mp4': (60., 60.),
    'Bellevue_150th_SE38th__2017-09-11_14-08-32.mp4': (60., 60.),
    'Bellevue_Bellevue_NE8th__2017-09-11_12-08-31.mp4': (60., 60.),
    'Bellevue_116th_NE12th__2017-09-10_23-08-29.mp4': (60., 60.),
}
DETECTOR = dict(conf=.1, imgsz=640, iou=.7, half=False, agnostic_nms=False,
                max_det=300, augment=False, rect=True, batch=1, classes=None)
TRACKER = dict(tracker_type='bytetrack', track_high_thresh=.35, track_low_thresh=.1,
               new_track_thresh=.35, track_buffer=30, match_thresh=.8, fuse_score=True)
CONTAINMENT_SHA = 'b299d573ecfcf8e609fc216f29b65ebcb288347def0e520cda404b29dcbf0ff6'
DURATION_LIMITS = {'Bellevue_150th_Eastgate': .2, 'Bellevue_150th_SE38th': 30.,
                   'Bellevue_Bellevue_NE8th': .11, 'Bellevue_116th_NE12th': 17.}
MAX_ATTEMPTS = 2
TRANSIENT_CODES = {75, -9}  # explicit temporary failure or proven killed worker


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.writing-' + uuid.uuid4().hex)
    with temp.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())
    temp.replace(path)


def fixed_json(path, value):
    if path.exists():
        if read(path) != value:
            raise ValueError(f'Existing generated configuration differs: {path}')
    else:
        atomic(path, value)


def immutable_completion(path, value, volatile=()):
    """A resumed delivery preserves original completion identity and timestamps."""
    path = Path(path)
    if path.exists():
        old = read(path)
        stable = lambda item: {k: v for k, v in item.items() if k not in set(volatile)}
        if stable(old) != stable(value):
            raise ValueError('Published completion identity changed; preserve for review: ' + str(path))
        return old
    atomic(path, value)
    return value


def within(root, relative):
    p = PurePosixPath(str(relative))
    if p.is_absolute() or '..' in p.parts or '\\' in str(relative):
        raise ValueError('Package paths must be relative and bounded')
    result = (root / str(p)).resolve()
    if root.resolve() not in result.parents:
        raise ValueError('Path escapes package root')
    current = root / str(p)
    while current != root:
        if current.is_symlink():
            raise ValueError('Package and output symlinks are forbidden')
        current = current.parent
    return result


def proc_token(pid):
    try:
        # Linux field 22; comm can contain whitespace and parentheses.
        return Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def live_owner(owner):
    if not owner:
        return False
    if owner.get('host') != socket.gethostname():
        return True  # cannot prove a foreign owner dead
    pid, token = owner.get('pid'), owner.get('proc_start_token')
    if not isinstance(pid, int) or not token:
        return True
    return proc_token(pid) == token


def inventory(root, paths):
    output = {}
    for path in paths:
        path = Path(path)
        items = sorted(path.rglob('*')) if path.is_dir() else [path]
        for item in items:
            if item.is_symlink():
                raise ValueError('Receipt cannot publish symbolic links')
            if item.is_file():
                relative = item.resolve().relative_to(root.resolve()).as_posix()
                output[relative] = {'bytes': item.stat().st_size, 'sha256': sha(item)}
    if not output:
        raise ValueError('No completed stage output files')
    return output


def verify_receipt(root, path, binding):
    receipt = read(path)
    if receipt.get('status') != 'completed' or receipt.get('binding') != binding:
        raise ValueError('Completed stage input binding changed; never silently rerun it')
    if not receipt.get('files'):
        raise ValueError('Empty stage completion receipt')
    for relative, expected in receipt['files'].items():
        target = within(root, relative)
        if not target.is_file() or target.stat().st_size != expected['bytes'] or sha(target) != expected['sha256']:
            raise ValueError('Completed stage output changed; preserved for review: ' + relative)
    if receipt.get('output_roots'):
        actual = inventory(root, [within(root, p) for p in receipt['output_roots']])
        if actual != receipt['files']:
            raise ValueError('Completed stage file inventory changed; preserve for review')
    return receipt


def output_roots(root, outputs):
    return [str(Path(p).resolve().relative_to(root.resolve())) for p in outputs]


def stage_timing(directory):
    executions, unknown = [], []
    for path in sorted(Path(directory).glob('attempt-*/execution.json')):
        value = read(path)
        elapsed = value.get('execution_wall_seconds')
        if isinstance(elapsed, (int, float)) and elapsed >= 0:
            executions.append({'attempt': path.parent.name, 'seconds': elapsed, 'returncode': value.get('returncode'),
                               'started_at': value.get('started_at'), 'ended_at': value.get('ended_at')})
        else:
            unknown.append(path.parent.name)
    return {'known_execution_wall_seconds': sum(row['seconds'] for row in executions),
            'complete_timing': not unknown, 'unknown_duration_attempts': unknown, 'executions': executions}


def verified_archive(archive, paths):
    """Publish/reuse only a hash-bound exact archive; never extract or delete."""
    archive = Path(archive)
    expected = {}
    for root in paths:
        root = Path(root)
        items = sorted(root.rglob('*')) if root.is_dir() else [root]
        for item in items:
            if item.is_symlink(): raise ValueError('Archive cannot include symbolic links')
            if item.is_file():
                key = (Path(root.name) / item.relative_to(root)).as_posix() if root.is_dir() else root.name
                if key in expected: raise ValueError('Archive member collision')
                expected[key] = {'bytes': item.stat().st_size, 'sha256': sha(item)}
    if not expected: raise ValueError('Empty archive input')
    receipt_path = archive.with_name(archive.name + '.receipt.json')
    binding = canonical(expected)
    if receipt_path.exists():
        receipt = read(receipt_path)
        if receipt.get('input_binding') != binding or not archive.exists() or sha(archive) != receipt.get('sha256'):
            raise ValueError('Published archive changed; preserve for review')
        return {k: receipt[k] for k in ('path', 'bytes', 'sha256')}
    if not archive.exists():
        partial = archive.with_name(archive.name + '.partial-' + uuid.uuid4().hex)
        with tarfile.open(partial, 'x:gz', compresslevel=3) as tar:
            for root in paths: tar.add(root, arcname=Path(root).name)
        partial.rename(archive)
    # Also permits safe adoption after a parent crash between archive and receipt.
    seen = {}
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or '\\' in member.name:
                raise ValueError('Unsafe archive member')
            if member.isdir(): continue
            if not member.isfile() or member.name in seen or member.name not in expected:
                raise ValueError('Unexpected archive member')
            handle = tar.extractfile(member); h = hashlib.sha256()
            for block in iter(lambda: handle.read(1024 * 1024), b''): h.update(block)
            seen[member.name] = {'bytes': member.size, 'sha256': h.hexdigest()}
    if seen != expected: raise ValueError('Archive content differs from verified stage inputs')
    receipt = {'status': 'completed', 'input_binding': binding, 'path': str(archive),
               'bytes': archive.stat().st_size, 'sha256': sha(archive), 'completed_at': now()}
    atomic(receipt_path, receipt)
    return {k: receipt[k] for k in ('path', 'bytes', 'sha256')}


def verify_package(root, job, expected_sha, *, verify_media=True):
    if sha(root / NEW_REL / 'runtime/job_manifest.json') != expected_sha:
        raise ValueError('Job manifest SHA differs from the explicitly approved package')
    if job.get('schema_version') != 'five_intersections_server_job_v1' or job.get('job_id') != 'five_intersections_20260927_r2':
        raise ValueError('Unsupported bounded job')
    if any(value is None for value in DURATION_LIMITS.values()):
        raise ValueError('Replacement candidate original timing has not yet been approved; execution remains blocked')
    sources = job.get('sources', [])
    if len(sources) != 4 or {s.get('source_id') for s in sources} != set(ALLOWED):
        raise ValueError('Exactly the four approved sources are required; Newport cannot be rerun')
    binding = job['selection']; selection_path = within(root, binding['path'])
    if sha(selection_path) != binding['sha256']:
        raise ValueError('Selection changed')
    selection = read(selection_path)
    new = [s for s in selection['entries'] if s.get('action') == 'new']
    reuse = [s for s in selection['entries'] if s.get('action') == 'reuse']
    if (selection.get('selection_frozen') is not True or len(new) != 4
            or {s['source_id'] for s in new} != set(ALLOWED) or len(reuse) != 1
            or reuse[0].get('camera_id') != 'Bellevue_150th_Newport'):
        raise ValueError('Selection must explicitly freeze four new sources and Newport reuse only')
    chosen = {s['source_id']: s for s in new}
    inputs = job.get('inputs', [])
    names = [x['path'] for x in inputs]
    if len(names) != len(set(names)) or not inputs:
        raise ValueError('Unique explicit packaged dependency hashes are required')
    for item in [job['split_manifest'], *inputs]:
        if sha(within(root, item['path'])) != item['sha256']:
            raise ValueError('Packaged dependency changed: ' + item['path'])
    for source in sources:
        ref = chosen[source['source_id']]
        for field in ('camera_id', 'split', 'source_sha256', 'source_bytes'):
            if source[field] != ref[field]:
                raise ValueError('Task differs from selection: ' + field)
        if source['split'] != 'Validation' or source['camera_id'] != source['source_id'].split('__')[0]:
            raise ValueError('Only the fixed Validation sources are permitted')
        if (source['short_start_seconds'], source['short_target_seconds']) != ALLOWED[source['source_id']]:
            raise ValueError('Short original-source bounds are not the predeclared check')
        config_path = within(root, source['full_config']['path'])
        if sha(config_path) != source['full_config']['sha256']:
            raise ValueError('Frozen camera full configuration changed')
        config = read(config_path)
        if (config.get('detector') != DETECTOR or config.get('tracker') != TRACKER
                or config.get('model', {}).get('sha256') != MODEL_SHA
                or config.get('model', {}).get('path') != MODEL_PATH
                or config.get('input', {}).get('kind') != 'video'
                or config['input'].get('source_id') != source['source_id']
                or config['input'].get('sha256') != source['source_sha256']
                or config.get('development_only') is not True or config.get('inference_enabled') is not True
                or config.get('segment') is not None
                or config.get('dedup', {}).get('mode') != 'cross_class_greedy_iou'
                or config['dedup'].get('iou_threshold') != .7
                or config['dedup'].get('containment_policy_sha256') != CONTAINMENT_SHA
                or config.get('time', {}).get('max_frame_duration_seconds') != DURATION_LIMITS[source['camera_id']]):
            raise ValueError('Frozen model/detector/tracker/source/full-scope settings differ')
        video = root / 'videos/original' / source['camera_id'] / source['source_id']
        if verify_media and (video.stat().st_size != source['source_bytes'] or sha(video) != source['source_sha256']):
            raise ValueError('Selected original source SHA/size differs')
    if verify_media and sha(MODEL_PATH) != MODEL_SHA:
        raise ValueError('Existing frozen checkpoint differs')
    return selection


def exact_segment(timing, audit, desired_start, desired_duration):
    tick = Fraction(audit['pts']['time_base']); first = audit['pts']['first_pts_ticks']
    low, high = Fraction(str(desired_start)), Fraction(str(desired_start + desired_duration))
    start = end = None
    with Path(timing).open() as f:
        for line in f:
            row = json.loads(line)
            pts = row.get('pts', row.get('pkt_pts'))
            if type(pts) is not int:
                raise ValueError('Original integer presentation timestamp required')
            value = (pts - first) * tick
            if start is None and value >= low:
                start = (row['frame_index'], value)
            if value <= high:
                end = (row['frame_index'], value)
    if start is None or end is None or end[0] <= start[0]:
        raise ValueError('Predeclared short check does not fit original source boundaries')
    return {'start_seconds': float(start[1]), 'end_seconds': float(end[1])}, {
        'desired_seconds': [float(low), float(high)], 'first_frame_index': start[0],
        'end_frame_index_exclusive': end[0], 'actual_seconds': [float(start[1]), float(end[1])],
        'policy': 'First original PTS at or after start; last original PTS at or before end; no transcode'}


class Pipeline:
    def __init__(self, root, job, package_sha, lock_fd):
        self.root, self.job, self.package_sha, self.lock_fd = root, job, package_sha, lock_fd
        self.new, self.system = root / NEW_REL, root / SYSTEM_REL
        self.started_at, self.current, self.child = now(), None, None
        self.env = dict(os.environ, PYTHONPATH=str(self.system / 'src'), YOLO_AUTOINSTALL='false', MPLBACKEND='Agg')
        self.render_env = dict(self.env, PYTHONPATH=PYAV_ROOT + ':' + self.env['PYTHONPATH'])
        self.selection = within(root, job['selection']['path'])

    def event(self, stage, **extra):
        value = dict(stage=stage, current_source=self.current, updated_at=now(), started_at=self.started_at,
                     pid=os.getpid(), session_id=os.getsid(0), process_group=os.getpgid(0),
                     host=socket.gethostname(), proc_start_token=proc_token(os.getpid()),
                     job_manifest_sha256=self.package_sha, child_pid=self.child.pid if self.child else None, **extra)
        atomic(self.new / 'batch_status.json', value)
        with (self.new / 'batch_events.jsonl').open('a') as f:
            f.write(json.dumps(value, ensure_ascii=False) + '\n')
        print(json.dumps(value, ensure_ascii=False), flush=True)

    def code_binding(self, role):
        result = {}
        for item in self.job['inputs']:
            path = item['path']; roles = item.get('roles')
            if roles is None:
                roles = ['inference'] if '/src/' in path else ['native'] if '/validation/' in path and 'time_audit' in path else ['qc'] if '/validation/' in path else ['render'] if '/presentation/' in path else ['runtime']
            # Camera configuration is bound separately per source; changing a later
            # camera must not invalidate already completed unrelated cameras.
            if '/config/' in path:
                continue
            if role in roles:
                actual = sha(within(self.root, path))
                if actual != item['sha256']:
                    raise ValueError('Stage dependency changed since package approval: ' + path)
                result[path] = actual
        if not result:
            raise ValueError('No packaged code bound for stage role: ' + role)
        return result

    def run_child(self, argv, attempt, phase, *, rendering=False, timeout=21600):
        state_path = attempt / 'execution.json'
        started = time.monotonic()
        with (attempt / 'stdout.log').open('xb') as log:
            self.child = subprocess.Popen(argv, cwd=self.system, env=self.render_env if rendering else self.env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                pass_fds=(self.lock_fd,))  # live child retains flock after parent crash
            owner = {'pid': self.child.pid, 'host': socket.gethostname(), 'proc_start_token': proc_token(self.child.pid)}
            record = dict(status='running', owner=owner, argv=argv, started_at=now(), phase=phase,
                          parent_pid=os.getpid(), child_session_id=os.getsid(self.child.pid))
            atomic(state_path, record)
            try:
                while self.child.poll() is None:
                    progress = None
                    if self.current:
                        camera = self.current.split('__')[0]
                        candidates = list((self.new / 'results' / camera / ('inference_' + phase)).glob('**/progress.json'))
                        if candidates:
                            try: progress = read(max(candidates, key=lambda p: p.stat().st_mtime_ns))
                            except (OSError, ValueError): pass
                    self.event('running_' + phase, progress=progress, stage_attempt=str(attempt), elapsed_stage_seconds=time.monotonic()-started)
                    if time.monotonic() - started > timeout:
                        raise TimeoutError('Bounded stage timeout; evidence retained')
                    try: self.child.wait(timeout=15)
                    except subprocess.TimeoutExpired: pass
                record.update(status='exited', returncode=self.child.returncode, ended_at=now(), execution_wall_seconds=time.monotonic()-started)
                atomic(state_path, record)
                return self.child.returncode
            finally:
                if self.child.poll() is None:
                    os.killpg(self.child.pid, signal.SIGTERM)
                    try: self.child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(self.child.pid, signal.SIGKILL); self.child.wait()
                    record.update(status='interrupted', returncode=self.child.returncode, ended_at=now(), execution_wall_seconds=time.monotonic()-started)
                    atomic(state_path, record)
                self.child = None

    def stage(self, camera, name, binding_data, build, finish, *, rendering=False):
        directory = self.new / 'results' / camera / 'stages' / name
        directory.mkdir(parents=True, exist_ok=True)
        binding = canonical(binding_data); receipt_path = directory / 'completion.json'
        if receipt_path.exists():
            receipt = verify_receipt(self.new, receipt_path, binding)
            self.event('skipped_verified_' + name)
            return receipt['result'], receipt_path
        attempts = sorted(directory.glob('attempt-*'))
        same_binding = []
        for old in attempts:
            if not (old / 'binding.json').exists():
                raise ValueError('Incomplete stage identity; preserve for review')
            if read(old / 'binding.json')['binding'] != binding:
                continue
            same_binding.append(old)
            execution = read(old / 'execution.json') if (old / 'execution.json').exists() else {}
            if execution.get('status') == 'running' and live_owner(execution.get('owner')):
                raise RuntimeError('A live or unverified child still owns the incomplete stage')
            # Adopt completed artifacts after a parent crash, without rerunning.
            try:
                result, outputs = finish(old)
            except (OSError, ValueError, KeyError):
                if execution.get('status') == 'exited' and execution.get('returncode') not in TRANSIENT_CODES:
                    raise RuntimeError('Prior non-transient stage failure requires a reviewed package fix: ' + name)
            else:
                receipt = dict(status='completed', binding=binding, result=result, files=inventory(self.new, outputs), output_roots=output_roots(self.new, outputs),
                               adopted_after_parent_restart=True, execution_timing=stage_timing(directory), completed_at=now(), job_manifest_sha256=self.package_sha)
                atomic(receipt_path, receipt)
                return result, receipt_path
        if len(same_binding) >= MAX_ATTEMPTS:
            raise RuntimeError('Bounded retry limit reached; prior evidence retained: ' + name)
        while len(same_binding) < MAX_ATTEMPTS:
            attempt = directory / f'attempt-{len(list(directory.glob("attempt-*")))+1:06d}'
            attempt.mkdir(); same_binding.append(attempt)
            atomic(attempt / 'binding.json', dict(binding=binding, inputs=binding_data))
            argv = build(attempt, len(same_binding) > 1)
            code = self.run_child(argv, attempt, name, rendering=rendering)
            if code != 0:
                if code in TRANSIENT_CODES and len(same_binding) < MAX_ATTEMPTS:
                    self.event('retrying_transient_' + name, prior_returncode=code); continue
                raise RuntimeError(f'{name} exited {code}; completed upstream stages retained')
            result, outputs = finish(attempt)
            receipt = dict(status='completed', binding=binding, result=result, files=inventory(self.new, outputs), output_roots=output_roots(self.new, outputs),
                           execution_timing=stage_timing(directory), completed_at=now(), job_manifest_sha256=self.package_sha)
            atomic(receipt_path, receipt)
            self.event('completed_' + name)
            return result, receipt_path

    def inference_finish(self, attempt, source):
        report = read(attempt / 'report.json')
        if report.get('failures') or len(report.get('rows', [])) != 1:
            raise ValueError('Expected exactly one successful inference task')
        row = report['rows'][0]
        if row.get('source_id') != source['source_id'] or row.get('split') != 'Validation' or row.get('status') not in {'completed', 'skipped_completed'}:
            raise ValueError('Completed task differs from approved source')
        target = Path(row['attempt_directory']).resolve()
        target.relative_to(self.new / 'results' / source['camera_id'])
        from full_video.batch import verify_completion
        okay, reason = verify_completion(target, row['fingerprint'], row['source_id'])
        if not okay: raise ValueError('Completed inference integrity failed: ' + reason)
        return {'attempt': str(target), 'fingerprint': row['fingerprint']}, [attempt / 'report.json', target]

    def camera(self, source):
        sid, camera = source['source_id'], source['camera_id']; self.current = sid
        camera_started = time.monotonic()
        base = self.new / 'results' / camera; base.mkdir(parents=True, exist_ok=True)
        media = self.root / 'videos/original' / camera / sid
        common = {'source_sha256': source['source_sha256'], 'source_id': sid, 'selection_sha256': self.job['selection']['sha256']}
        def native_finish(a):
            output = a / 'output'; report = read(output / 'audit_runtime_envelope.json')
            if report.get('status') != 'completed' or report.get('source_sha256') != source['source_sha256'] or report.get('task', {}).get('source_id') != sid:
                raise ValueError('Native audit did not complete for selected source')
            return {'directory': str(output)}, [output]
        native, native_receipt = self.stage(camera, 'native', {**common, 'code': self.code_binding('native')},
            lambda a, retry: [PYTHON, str(self.new / 'validation/prepare_selected_time_audit.py'), '--selection', str(self.selection), '--source-id', sid,
                '--source', str(media), '--manifest', str(within(self.root, self.job['split_manifest']['path'])), '--ffprobe', '/usr/bin/ffprobe',
                '--output', str(a / 'output'), '--src-root', str(self.system / 'src')], native_finish)
        audit_dir = Path(native['directory']); timing = audit_dir / 'timing_frames.jsonl'
        audit = read(audit_dir / 'audit_runtime_envelope.json')
        segment, segment_receipt = exact_segment(timing, audit, source['short_start_seconds'], source['short_target_seconds'])
        fixed_json(base / 'short_original_interval.json', segment_receipt)
        from full_video.configuration import load_config, camera_for
        config = load_config(within(self.root, source['full_config']['path']))
        if Path(config['source_root']) != self.root / 'videos/original' or Path(config['split_manifest']) != within(self.root, self.job['split_manifest']['path']):
            raise ValueError('Configuration source/manifest roots differ from fixed job')
        bound_camera = camera_for(config, source)
        if bound_camera['source_ids'] != [sid] or bound_camera['background_filter']['mode'] != 'none':
            raise ValueError('New cameras require exact single-source geometry and explicit no-background-mask mode')
        camera_identity = canonical(bound_camera)
        config['time'] = {**config.get('time', {}), 'mode': 'audited_pts', 'ffprobe_path': '/usr/bin/ffprobe',
                          'audit_summary_path': str(audit_dir / 'audit_runtime_envelope.json'), 'frame_timing_path': str(timing)}
        results = {}; receipts = {'native': str(native_receipt)}
        def infer_command(a, retry, conf):
            # A corrupted completed attempt is evidence, never a reason to rerun inference.
            from full_video.batch import verify_completion
            output_root = Path(read(conf)['output_root'])
            for old in output_root.glob('*.mp4/*/attempt-*'):
                state_file = old / 'status.json'
                if state_file.exists() and read(state_file).get('status') == 'completed':
                    state = read(state_file)
                    okay, reason = verify_completion(old, state['fingerprint'], sid)
                    if not okay: raise ValueError('Refusing to rerun changed completed inference: ' + reason)
            return [PYTHON, '-m', 'full_video', 'retry-failed' if retry else 'run', '--recover-interrupted',
                    '--config', str(conf), '--source', sid, '--limit', '1', '--report', str(a / 'report.json')]
        for phase in ('short', 'full'):
            effective = json.loads(json.dumps(config)); effective['segment'] = segment if phase == 'short' else None
            effective['output_root'] = str(base / ('inference_' + phase))
            effective['run_version'] = config['run_version'] + '-' + phase
            conf = base / ('effective_' + phase + '.json'); fixed_json(conf, effective)
            binding = {**common, 'configuration_sha256': sha(conf), 'camera_identity_sha256': camera_identity, 'native_receipt_sha256': sha(native_receipt), 'code': self.code_binding('inference')}
            result, receipt = self.stage(camera, phase, binding,
                lambda a, retry, conf=conf: infer_command(a, retry, conf),
                lambda a: self.inference_finish(a, source))
            results[phase], receipts[phase] = result, str(receipt)
            def qc_finish(a):
                output = a / 'output'; qc = read(output / 'qc_summary.json')
                if qc.get('status') != 'passed' or qc.get('error_count') != 0:
                    raise ValueError('Independent consistency audit did not pass')
                if qc.get('source_id') != sid:
                    raise ValueError('QC source identity differs')
                return {'directory': str(output), 'summary': str(output / 'qc_summary.json')}, [output]
            qc, qc_receipt = self.stage(camera, phase + '_qc', {**common, 'input_receipt_sha256': sha(receipt), 'code': self.code_binding('qc')},
                lambda a, retry, phase=phase, result=result: [PYTHON, str(self.new / 'validation/audit_selected_output.py'), '--selection', str(self.selection),
                    '--source-id', sid, '--attempt', result['attempt'], '--pts', str(timing), '--output', str(a / 'output'), '--phase', phase, '--plot'], qc_finish)
            results[phase + '_qc'], receipts[phase + '_qc'] = qc, str(qc_receipt)
            if phase == 'short':
                def contacts_finish(a):
                    output = a / 'contacts'; report = read(output / 'contact_receipt.json')
                    if report.get('state') != 'complete' or report.get('source_id') != sid or report.get('pixels_verified') is not True:
                        raise ValueError('Short visual evidence is not source-verified')
                    return {'directory': str(output)}, [output]
                contacts, contacts_receipt = self.stage(camera, 'short_contacts', {**common,
                    'short_receipt_sha256': sha(receipt), 'qc_receipt_sha256': sha(qc_receipt),
                    'code': self.code_binding('render'), 'helper_sha256': sha(self.new / 'runtime/short_contacts.py')},
                    lambda a, retry: [PYTHON, str(self.new / 'runtime/short_contacts.py'), '--selection', str(self.selection),
                        '--source-id', sid, '--source', str(media), '--attempt', result['attempt'],
                        '--output', str(a / 'contacts'), '--src-root', str(self.system / 'src')], contacts_finish)
                results['short_contacts'], receipts['short_contacts'] = contacts, str(contacts_receipt)
        full_attempt = Path(results['full']['attempt'])
        provenance = read(full_attempt / 'provenance.json')
        # Re-serializing geometry changes file SHA, so use the exact approved layout file.
        layout_path = Path(provenance['camera']['layout_path'])
        def render_finish(a):
            receipt = read(a / 'render_receipt.json'); video = a / 'annotated.mp4'
            if receipt.get('state') != 'complete' or receipt.get('source_id') != sid or receipt.get('source_decoded_pixel_sha256_checked') is not True:
                raise ValueError('Presentation verification incomplete')
            if sha(video) != receipt['output_sha256']:
                raise ValueError('Presentation video digest mismatch')
            return {'video': str(video), 'receipt': str(a / 'render_receipt.json')}, [video, a / 'render_receipt.json']
        render, render_receipt = self.stage(camera, 'render', {**common, 'inference_receipt_sha256': sha(receipts['full']),
            'qc_receipt_sha256': sha(receipts['full_qc']), 'code': self.code_binding('render')},
            lambda a, retry: [PYTHON, str(self.new / 'presentation/render_selected_pts.py'), '--selection', str(self.selection), '--source-id', sid,
                '--source', str(media), '--records', str(full_attempt / 'frames.jsonl'), '--provenance', str(full_attempt / 'provenance.json'),
                '--zones', str(layout_path), '--output', str(a / 'annotated.mp4'), '--metadata', str(a / 'render_receipt.json'),
                '--src-root', str(self.system / 'src'), '--verify-source-pixels'], render_finish, rendering=True)
        receipts['render'] = str(render_receipt)
        delivery = base / 'delivery'; delivery.mkdir(exist_ok=True)
        files = {}
        for name, paths in [('summary', [Path(results['full_qc']['directory']), base / 'short_original_interval.json', Path(render['receipt']), Path(results['short_contacts']['directory'])]),
                            ('data', [full_attempt, audit_dir])]:
            archive = delivery / (name + '.tar.gz')
            files[name + '_archive'] = verified_archive(archive, paths)
        for name, path in [('annotated.mp4', Path(render['video'])), ('render_receipt.json', Path(render['receipt']))]:
            destination = delivery / name
            if not destination.exists(): os.link(path, destination)
            if sha(path) != sha(destination): raise ValueError('Published presentation differs; preserve for review')
            files[name] = {'path': str(destination), 'bytes': destination.stat().st_size, 'sha256': sha(destination)}
        done = {'status': 'completed', **common, 'camera_id': camera, 'split': 'Validation', 'completed_at': now(),
            'full_frames': provenance['preflight']['input_frame_count'], 'full_duration_seconds': provenance['preflight']['input_duration_seconds'],
            'stage_receipts': receipts, 'files': files, 'main_test_media_read': False,
            'execution_timing': {name: read(path).get('execution_timing', {}) for name, path in receipts.items()},
            'camera_current_invocation_wall_seconds': time.monotonic() - camera_started,
            'camera_subprocess_wall_seconds': sum(read(path).get('execution_timing', {}).get('known_execution_wall_seconds', 0) for path in receipts.values()),
            'timing_note': 'Subprocess wall time includes retries but excludes offline waits; it is not GPU kernel time. Current invocation includes verification and packaging.',
            'limits': ['Engineering consistency is not accuracy validation.', 'Current visible vehicles are not cumulative traffic flow.',
                       'The short check and full source overlap; neither is a new independent holdout.']}
        return immutable_completion(base / 'camera_completion.json', done,
            volatile=('completed_at', 'camera_current_invocation_wall_seconds'))

    def run(self):
        self.event('started', detached_session=os.getsid(0) == os.getpid(), restart_policy='verified completed stages skip; incomplete inference restarts whole interval')
        def smoke_finish(a):
            output = a / 'output'; report = read(output / 'codec_smoke_report.json')
            if report.get('state') != 'passed': raise ValueError('Actual VFR codec smoke failed')
            return {'report': str(output / 'codec_smoke_report.json')}, [output]
        self.stage('_runtime', 'codec_smoke', {'code': self.code_binding('render'), 'pyav_root': PYAV_ROOT},
            lambda a, retry: [PYTHON, str(self.new / 'presentation/smoke_generated_pts.py'),
                '--output-dir', str(a / 'output'), '--src-root', str(self.system / 'src')], smoke_finish, rendering=True)
        completed = []
        for source in self.job['sources']:
            completed.append(self.camera(source))
        selection = read(self.selection)
        result = {'status': 'completed', 'job_id': self.job['job_id'], 'job_manifest_sha256': self.package_sha, 'completed_at': now(),
                  'new_completed_count': 4, 'sources': completed, 'newport': next(x for x in selection['entries'] if x['action'] == 'reuse'),
                  'newport_inference_rerun': False, 'main_test_media_read': False}
        immutable_completion(self.new / 'batch_completion.json', result, volatile=('completed_at',))
        self.event('completed', completion=str(self.new / 'batch_completion.json'))


def stop(signum, frame):
    raise KeyboardInterrupt('Authorized process interrupted; stage evidence retained')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-job-sha256', required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch('[a-f0-9]{64}', args.expected_job_sha256): parser.error('A full approved manifest SHA256 is required')
    if not args.validate_only and Path(__file__).resolve() != REMOTE_ROOT / NEW_REL / 'runtime/server_pipeline.py':
        parser.error('Execution is restricted to the fixed isolated Linux server job root')
    root = Path(__file__).resolve().parents[5]
    new = root / NEW_REL; job = read(new / 'runtime/job_manifest.json')
    with (new / 'runtime/server_pipeline.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Pipeline or inherited live child owns the job lock; no duplicate started.'); return 10
        verify_package(root, job, args.expected_job_sha256, verify_media=not args.validate_only)
        if args.validate_only:
            print(json.dumps({'status': 'validated', 'new_sources': 4, 'inference_started': False})); return 0
        sys.path.insert(0, str(root / SYSTEM_REL / 'src'))
        pipeline = Pipeline(root, job, args.expected_job_sha256, lock.fileno())
        signal.signal(signal.SIGHUP, signal.SIG_IGN); signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
        try: pipeline.run()
        except BaseException as error:
            pipeline.event('failed', error=repr(error), traceback=traceback.format_exc()); raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
