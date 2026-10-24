"""Derive fixed-ROI count diagnostics from existing validation boxes only."""
from pathlib import Path
from collections import Counter, defaultdict
import ast
import csv
import hashlib
import json
import math
import numpy as np

BASE = Path('/Users/zoki/Desktop/traffic_projects/formal_research')
OUT = Path(__file__).resolve().parent
RUNS = BASE / 'traffic_yolo_system/outputs'
VAL = RUNS / 'validation_tuning/yolo11s_confidence_val_v1_2026-09-23'
LABELS = BASE / '05_full_dataset/formal_yolo_trainval_v1/labels/val'
PROTOCOL = json.loads((OUT / 'protocol.json').read_text())

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

for name, info in PROTOCOL['inputs'].items():
    assert digest(Path(name)) == info['sha256'], name

# Execute only the five pure geometry functions from the frozen implementation.
# Its OpenCV branch is legacy-only; every selected layout uses zone_order.
geometry_path = RUNS / 'supplementary_five_2026-09-28_v1/code/src/roi_geometry.py'
tree = ast.parse(geometry_path.read_text())
names = {'_cross', '_on_segment', '_float64_point_position', 'assign_point', 'assign_box'}
selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
assert len(selected) == len(names)
scope = {'np': np, 'GEOMETRY_EPSILON': 1e-9}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(geometry_path), 'exec'), scope)

def independent_position(point, polygon):
    """Independent winding-number check, with the frozen boundary tolerance."""
    x, y = point
    winding = 0
    for i, (ax, ay) in enumerate(polygon):
        bx, by = polygon[(i + 1) % len(polygon)]
        determinant = (bx-ax)*(y-ay) - (by-ay)*(x-ax)
        if abs(determinant) <= 1e-9 and min(ax,bx)-1e-9 <= x <= max(ax,bx)+1e-9 and min(ay,by)-1e-9 <= y <= max(ay,by)+1e-9:
            return 0
        if ay <= y < by and determinant > 0:
            winding += 1
        elif by <= y < ay and determinant < 0:
            winding -= 1
    return 1 if winding else -1

layouts = {}
for p in sorted((RUNS / 'supplementary_five_2026-09-28_v1/config').glob('*/roi.json')):
    layout = json.loads(p.read_text())
    assert layout['assignment_point'] == 'bottom_center'
    assert layout['boundary_tie_break'] == 'zone_order'
    layouts[layout['camera_id']] = layout
assert len(layouts) == 5 and sum(len(x['zones']) for x in layouts.values()) == 22

def assign(box, layout):
    polygons = [(z['zone_name'], np.asarray(z['polygon_points'],dtype=np.float64)) for z in layout['zones']]
    actual = scope['assign_box'](box, polygons, 'zone_order')
    point = ((box[0]+box[2])/2, box[3])
    hits = [(name, independent_position(point, polygon)) for name, polygon in polygons]
    hits = [(name, pos) for name,pos in hits if pos >= 0]
    assert len(hits) <= 1 or all(pos == 0 for _,pos in hits), ('interior overlap',point,hits)
    expected = hits[0][0] if hits else 'outside_roi'
    assert expected == actual, (point, expected, actual)
    return actual

records = [json.loads(line) for line in (VAL / 'results/predictions_and_matches.jsonl').read_text().splitlines()]
manifest = json.loads((VAL / 'input/validation_manifest.json').read_text())
assert len(records) == len(manifest['images']) == 197
assert len({r['filename'] for r in records}) == 197
assert {r['filename'] for r in records} == {r['filename'] for r in manifest['images']}
metadata = {r['filename']:r for r in manifest['images']}
region_rows, frame_rows, assignments = [], [], []
label_hashes, source_ids, gt_classes = {}, set(), Counter()
for record in sorted(records,key=lambda x:x['filename']):
    meta = metadata[record['filename']]
    assert record['metadata'] == meta
    assert meta['split'] == 'Validation'
    camera = meta['intersection']
    layout = layouts[camera]
    assert (record['width'],record['height']) == (layout['frame_width'],layout['frame_height']) == (1280,720)
    label_path = LABELS / (Path(record['filename']).stem + '.txt')
    actual_hash = digest(label_path)
    assert actual_hash == meta['label_sha256']
    label_hashes[str(label_path)] = actual_hash
    label_lines = [line.split() for line in label_path.read_text().splitlines() if line.strip()]
    assert len(label_lines) == len(record['ground_truth'])
    for vals,gt in zip(label_lines,record['ground_truth']):
        assert int(vals[0]) == gt['class_id']
        assert np.allclose([float(v) for v in vals[1:]],gt['box_xywh_normalized'],rtol=0,atol=1e-10)
        xc,yc,w,h = map(float,vals[1:])
        converted = [(xc-w/2)*1280,(yc-h/2)*720,(xc+w/2)*1280,(yc+h/2)*720]
        assert np.allclose(converted,gt['box_xyxy'],rtol=0,atol=1e-7)
    gt = record['ground_truth']
    pred = record['thresholds']['0.35']['detections']
    assert all(p['confidence'] >= .35-1e-8 for p in pred)
    counts = {}
    for kind,boxes in [('gt',gt),('pred',pred)]:
        counts[kind] = Counter()
        for box in boxes:
            assert box['class_id'] in range(4)
            region = assign(box['box_xyxy'],layout)
            counts[kind][region] += 1
            assignments.append({'image':record['filename'],'camera':camera,'kind':kind,'box_index':box['index'],'class_id':box['class_id'],'region':region})
            if kind == 'gt':gt_classes[box['class_name']] += 1
    for region in [z['zone_name'] for z in layout['zones']]:
        g,p = counts['gt'][region],counts['pred'][region]
        region_rows.append({'image':record['filename'],'source_video':meta['source_video'],'camera':camera,'lighting':meta['lighting'],'region':region,'gt_count':g,'pred_count':p,'error':p-g,'absolute_error':abs(p-g)})
    source_ids.add(meta['source_video'])
    frame_rows.append({'image':record['filename'],'source_video':meta['source_video'],'camera':camera,'lighting':meta['lighting'],'gt_whole_image':len(gt),'pred_whole_image':len(pred),'gt_outside_roi':counts['gt']['outside_roi'],'pred_outside_roi':counts['pred']['outside_roi'],'gt_count':len(gt)-counts['gt']['outside_roi'],'pred_count':len(pred)-counts['pred']['outside_roi']})
assert dict(gt_classes) == manifest['expected_gt_counts']
assert len(source_ids) == 17

def metrics(rows):
    errors = [r['pred_count']-r['gt_count'] for r in rows]
    return {'n':len(rows),'gt_count_sum':sum(r['gt_count'] for r in rows),'pred_count_sum':sum(r['pred_count'] for r in rows),'absolute_error_sum':sum(abs(e) for e in errors),'signed_error_sum':sum(errors),'MAE':sum(abs(e) for e in errors)/len(rows),'bias':sum(errors)/len(rows),'RMSE':math.sqrt(sum(e*e for e in errors)/len(rows)),'exact_count_n':sum(e==0 for e in errors),'exact_count_fraction':sum(e==0 for e in errors)/len(rows),'gt_nonzero_n':sum(r['gt_count']>0 for r in rows)}

by_camera = []
for camera in sorted(layouts):
    rr = [r for r in region_rows if r['camera']==camera]
    ff = [r for r in frame_rows if r['camera']==camera]
    by_camera.append({'camera':camera,'images':len(ff),'source_videos':len({r['source_video'] for r in ff}),'regions':len(layouts[camera]['zones']),'roi_image_metrics':metrics(rr),'in_roi_sum_per_image_metrics':metrics(ff)})
whole_rows = [{'gt_count':r['gt_whole_image'],'pred_count':r['pred_whole_image']} for r in frame_rows]
summary = {'evaluation':PROTOCOL['evaluation_level'],'confidence':.35,'images':197,'source_videos':17,'cameras':5,'regions':22,'roi_image_pairs':len(region_rows),'all_roi_image_metrics':metrics(region_rows),'in_roi_sum_per_image_metrics':metrics(frame_rows),'whole_image_metrics':metrics(whole_rows),'by_camera':by_camera,'outside_roi':{'ground_truth_boxes':sum(r['gt_outside_roi'] for r in frame_rows),'prediction_boxes':sum(r['pred_outside_roi'] for r in frame_rows)},'ground_truth_class_counts':dict(gt_classes),'independent_assignment_check':'Frozen float64 ray casting and independent winding-number implementation agreed for every GT and predicted bottom-centre point','label_integrity':'All 197 label hashes and every saved GT normalized/pixel box checked against retained human-corrected labels','new_training':False,'new_inference':False,'filters_or_tracking_evaluated':False,'new_annotations':False,'protocol_sha256':digest(OUT/'protocol.json'),'script_sha256':digest(Path(__file__))}
for name,rows in [('roi_image_errors.csv',region_rows),('frame_counts.csv',frame_rows),('box_assignments.csv',assignments)]:
    with (OUT/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
(OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
(OUT/'label_hashes.json').write_text(json.dumps(label_hashes,indent=2)+'\n')
for name,info in PROTOCOL['inputs'].items():
    assert digest(Path(name)) == info['sha256'],('input changed',name)
print(json.dumps({k:summary[k] for k in ['images','roi_image_pairs','all_roi_image_metrics','by_camera','outside_roi','whole_image_metrics']},indent=2))
