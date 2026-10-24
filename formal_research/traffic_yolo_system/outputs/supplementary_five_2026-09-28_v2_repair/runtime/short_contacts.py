#!/usr/bin/env python3
"""Three exact analysed original frames for a bounded visual development check."""
from pathlib import Path
import argparse
import hashlib
import json
import math
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('selection', 'source', 'attempt', 'output', 'src-root'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--source-id', required=True)
    args = p.parse_args()
    new = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(new / 'validation'))
    sys.path.insert(0, str(new / 'presentation'))
    sys.path.insert(0, str(args.src_root))
    from selection_binding import selected_entry, bind_prepared, sha
    from render_selected_preview import original_capture, draw_panel
    from render_video_tracking_pilot import read_zones, render_frame, validate_record, text
    entry = selected_entry(args.selection, args.source_id)
    provenance = json.loads((args.attempt / 'provenance.json').read_text())
    task, config, camera, preflight = bind_prepared(entry, provenance)
    if config.get('segment') is None or args.source.name != args.source_id or sha(args.source) != entry['source_sha256']:
        raise ValueError('Only the selected source and its completed short interval are permitted')
    records_path = args.attempt / 'frames.jsonl'
    rows = [json.loads(line) for line in records_path.open()]
    if len(rows) != preflight['frame_count'] or not rows:
        raise ValueError('Incomplete short records')
    positions = sorted({0, len(rows) // 2, len(rows) - 1})
    selected = {rows[i]['frame_index']: rows[i] for i in positions}
    layout = Path(camera['layout_path'])
    if sha(layout) != camera['layout_sha256']:
        raise ValueError('Road geometry changed')
    if args.output.exists(): raise FileExistsError('Contact evidence output already exists')
    args.output.mkdir(parents=True)
    import cv2
    import numpy as np
    zones = read_zones(layout)
    tick = preflight['time_base_num'] / preflight['time_base_den']
    first_msec, previous_msec, images, details = None, None, [], []
    with original_capture(cv2, args.source, preflight.get('audited_decoder', {}).get('requested_decoder_threads', 2)) as capture:
        for index in range(max(selected) + 1):
            okay, pixels = capture.read()
            if not okay: raise ValueError('Source decoder ended before short evidence frame')
            next_index, msec = float(capture.get(cv2.CAP_PROP_POS_FRAMES)), float(capture.get(cv2.CAP_PROP_POS_MSEC))
            if not math.isfinite(next_index) or abs(next_index - index - 1) > 1e-6:
                raise ValueError('Original decoder frame index differs')
            if not math.isfinite(msec) or (previous_msec is not None and msec <= previous_msec):
                raise ValueError('Original decoder PTS is not strictly increasing')
            if first_msec is None: first_msec = msec
            previous_msec = msec
            if index not in selected: continue
            row = selected[index]
            if abs((msec - first_msec) / 1000 - row['source_timestamp_seconds']) > tick + 1e-7:
                raise ValueError('Original frame PTS differs from analysed frame')
            pixels_sha = hashlib.sha256(np.ascontiguousarray(pixels).tobytes()).hexdigest()
            if pixels_sha != row.get('decoded_frame_sha256') or row.get('configuration_sha256') != provenance['fingerprint']:
                raise ValueError('Short evidence does not match exact analysed pixels/configuration')
            validate_record(row, index, zones, preflight['width'], preflight['height'])
            canvas = render_frame(pixels, row, zones, {}, preflight['fps_num'] / preflight['fps_den'], preflight['width'])
            draw_panel(canvas, row, zones, task['camera_id'], cv2, text)
            name = f'frame-{index:08d}.png'; destination = args.output / name
            if not cv2.imwrite(str(destination), canvas): raise OSError('PNG write failed')
            images.append(cv2.resize(canvas, (960, round(canvas.shape[0] * 960 / canvas.shape[1]))))
            details.append({'frame_index': index, 'source_timestamp_seconds': row['source_timestamp_seconds'],
                'duration_seconds': row['duration_seconds'], 'decoded_frame_sha256': pixels_sha,
                'path': name, 'sha256': sha(destination)})
    sheet = args.output / 'short_contact_sheet.jpg'
    if not cv2.imwrite(str(sheet), np.concatenate(images, axis=0), [cv2.IMWRITE_JPEG_QUALITY, 92]):
        raise OSError('Contact sheet write failed')
    report = {'state': 'complete', 'source_id': args.source_id, 'split': entry['split'], 'pixels_verified': True,
        'selection_sha256': sha(args.selection), 'records_sha256': sha(records_path),
        'provenance_sha256': sha(args.attempt / 'provenance.json'), 'frames': details,
        'contact_sheet_sha256': sha(sheet), 'meaning': 'Visual development evidence; no claim of manual accuracy validation.'}
    (args.output / 'contact_receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0

if __name__ == '__main__': raise SystemExit(main())
