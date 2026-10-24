"""Compare a new spatial assignment against frozen records, without inference."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import csv
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def read(p):
    return json.loads(p.read_text())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def independent_membership(point, polygon):
    """Ray crossing with a separate boundary check; no OpenCV assignment call."""
    x, y = point
    inside = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        cross = (x-a[0])*(b[1]-a[1])-(y-a[1])*(b[0]-a[0])
        if abs(cross) < 1e-8 and min(a[0], b[0]) <= x <= max(a[0], b[0]) and min(a[1], b[1]) <= y <= max(a[1], b[1]):
            return 0
        if (a[1] > y) != (b[1] > y):
            intercept = a[0] + (y-a[1])*(b[0]-a[0])/(b[1]-a[1])
            if x < intercept:
                inside = not inside
    return 1 if inside else -1


def independent_assign(box, zones):
    pt = ((box[0]+box[2])/2, box[3])
    hits = [(z['zone_name'], independent_membership(pt, z['polygon_points'])) for z in zones]
    hits = [(name, side) for name, side in hits if side >= 0]
    assert len(hits) < 2 or all(side == 0 for _, side in hits), hits
    return hits[0][0] if hits else 'outside_roi'


def without_assignments(rec):
    item = copy.deepcopy(rec)
    for key in ['zone_counts', 'total_in_zones', 'outside_zone_count', 'reference_zone_counts', 'reference_total_in_zones']:
        item.pop(key, None)
    for branch in ['primary', 'high_input']:
        for track in item['tracks_'+branch]:
            track.pop('assigned_zone', None)
    return item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--revision', type=Path, required=True)
    args = parser.parse_args()
    old, new = args.previous.resolve(), args.revision.resolve()
    layout, old_layout = read(new/'input/zones.json'), read(old/'input/zones.json')
    assert layout['zones'][:3] == old_layout['zones'][:3]
    assert layout['boundary_tie_break'] == 'zone_order'
    assert sha(old/'input/zones.json') == layout['revision']['previous_layout_sha256']
    changes, frame_deltas = [], []
    selected = {}
    gaps = read(old/'count_review_2026-09-24/boundary_review.json')['cases']
    examined_frames = {450, 1312, 1315, 1317, 1320, 1321, 1324, 1350, 1595, 1950, 2137, 2158, 2550, 2850, 3450}
    for c in gaps:
        interval = c['outside_interval']
        examined_frames.update(range(interval['first_frame_inclusive']-1, interval['last_frame_inclusive']+2))
    totals = Counter()
    with (old/'analysis_vehicle_dedup/frames_with_zones.jsonl').open() as before, (new/'analysis/frames_with_zones.jsonl').open() as after:
        index = 0
        while True:
            line_a, line_b = before.readline(), after.readline()
            assert bool(line_a) == bool(line_b), 'Frame count differs'
            if not line_a:
                break
            a, b = json.loads(line_a), json.loads(line_b)
            assert a['frame_index'] == b['frame_index'] == index
            assert without_assignments(a) == without_assignments(b), 'Detector/track content changed'
            for branch in ['primary', 'high_input']:
                counts = Counter()
                for tr in b['tracks_'+branch]:
                    zone = independent_assign(tr['box_xyxy'], layout['zones'])
                    assert zone == tr['assigned_zone'], (index, branch, tr['track_id'])
                    counts[zone] += 1
                    totals['independent_track_assignments_verified'] += 1
                key = 'zone_counts' if branch == 'primary' else 'reference_zone_counts'
                assert b[key] == {z['zone_name']:counts[z['zone_name']] for z in layout['zones']}
            for zone in ['road_left', 'road_top', 'road_right']:
                assert a['zone_counts'][zone] == b['zone_counts'][zone], 'Approach counts changed'
            for ta, tb in zip(a['tracks_primary'], b['tracks_primary']):
                if ta['assigned_zone'] != tb['assigned_zone']:
                    assert ta['assigned_zone'] == 'outside_roi' and tb['assigned_zone'] == 'junction_center'
                    changes.append({'frame_index':index, 'clip_seconds':index/30, 'track_id':ta['track_id'], 'before':ta['assigned_zone'], 'after':tb['assigned_zone'], 'confidence':ta['confidence']})
            delta = b['total_in_zones'] - a['total_in_zones']
            assert delta >= 0 and delta == a['outside_zone_count'] - b['outside_zone_count']
            frame_deltas.append({'frame_index':index, 'clip_seconds':index/30, 'center_before':a['zone_counts']['junction_center'], 'center_after':b['zone_counts']['junction_center'], 'total_before':a['total_in_zones'], 'total_after':b['total_in_zones'], 'additional_current_observations':delta})
            if index in examined_frames:
                selected[index] = {'before':a, 'after':b}
            index += 1
    assert index == 3600
    case_results = []
    for c in gaps:
        interval = c['outside_interval']; rows=[]
        for fr in range(interval['first_frame_inclusive']-1, interval['last_frame_inclusive']+2):
            row={'frame':fr, 'clip_seconds':fr/30}
            for version in ['before','after']:
                track=next(tr for tr in selected[fr][version]['tracks_primary'] if tr['track_id']==c['track_id'])
                row[version]=track['assigned_zone']
            rows.append(row)
        during=rows[1:-1]
        result={'case_id':c['case_id'],'track_id':c['track_id'],'before_outside_frames':sum(r['before']=='outside_roi' for r in during),'after_outside_frames':sum(r['after']=='outside_roi' for r in during),'rows':rows}
        if c['case_id']=='case1_spatial_gap':
            assert result['before_outside_frames']==6 and result['after_outside_frames']==0
        else:
            assert all(r['before']==r['after'] for r in rows), 'Outer-margin cases must not be masked by this change'
        case_results.append(result)
    old_manifest=read(old/'job/delivery_manifest.json')
    unchanged={name:sha(old/name)==meta['sha256'] for name,meta in old_manifest['files'].items()}
    assert all(unchanged.values())
    controls={str(fr/30): {'before':selected[fr]['before']['zone_counts'], 'after':selected[fr]['after']['zone_counts']} for fr in [450,1350,1950,2550,2850,3450]}
    verification={
        'status':'passed', 'frames_compared':index, **dict(totals),
        'detector_and_tracker_records_unchanged':True, 'old_deliverables_unchanged':unchanged,
        'approach_counts_unchanged_all_frames':True, 'change_types':['outside_roi -> junction_center'],
        'changed_frames':sum(r['additional_current_observations']>0 for r in frame_deltas),
        'added_track_frame_observations':len(changes),
        'added_vehicle_observation_seconds':len(changes)/30,
        'affected_track_ids_not_unique_vehicle_count':len({r['track_id'] for r in changes}),
        'meaning':'Additional coverage in real-road connector bands; not new detections, unique cars, improved accuracy or congestion ground truth.',
        'boundary_cases':case_results, 'six_fixed_time_controls':controls,
        'known_background_false_positives_unchanged_at_85_and_95':True,
        'distant_tracking_and_outer_border_jitter_not_fixed':True,
        'main_test_used':False, 'inference_or_training_run':False,
    }
    (new/'job/roi_revision_verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n')
    for name, data in [('assignment_changes.csv',changes), ('frame_count_comparison.csv',frame_deltas)]:
        with (new/'analysis'/name).open('x',newline='') as out:
            writer=csv.DictWriter(out,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)
    # A compact visual check of the same SUV before/during/after the former gap.
    cap=cv2.VideoCapture(layout['clip_file'])
    font=ImageFont.truetype('/System/Library/Fonts/STHeiti Medium.ttc',22)
    small=ImageFont.truetype('/System/Library/Fonts/STHeiti Light.ttc',17)
    cols=[1312,1317,1321];canvas=Image.new('RGB',(1440,790),'white');draw=ImageDraw.Draw(canvas)
    labels={'road_left':'左侧道路','junction_center':'路口中央','outside_roi':'未计入任何区域'}
    for column, fr in enumerate(cols):
        cap.set(cv2.CAP_PROP_POS_FRAMES,fr);ok,raw=cap.read();assert ok
        for row,version in enumerate(['before','after']):
            zones=old_layout if version=='before' else layout
            img=raw.copy()
            for zone in zones['zones']:
                cv2.polylines(img,[np.asarray(zone['polygon_points'],np.int32)],True,zone['color_bgr'],2,cv2.LINE_AA)
            tr=next(tr for tr in selected[fr][version]['tracks_primary'] if tr['track_id']==2)
            x1,y1,x2,y2=tr['box_xyxy'];pt=(round((x1+x2)/2),round(y2))
            cv2.rectangle(img,(round(x1),round(y1)),(round(x2),round(y2)),(255,255,255),2)
            cv2.circle(img,pt,5,(255,80,220),-1)
            crop=img[185:375,200:470];crop=cv2.resize(crop,(480,338),interpolation=cv2.INTER_CUBIC)
            x,y=column*480,row*395
            canvas.paste(Image.fromarray(cv2.cvtColor(crop,cv2.COLOR_BGR2RGB)),(x,y+57))
            draw.text((x+12,y+7),('修改前' if row==0 else '修改后')+f' · {fr/30:.2f}秒',font=font,fill='#172331')
            draw.text((x+12,y+34),labels[tr['assigned_zone']],font=small,fill='#a52535' if tr['assigned_zone']=='outside_roi' else '#206441')
    cap.release();canvas.save(new/'分区接缝修正示例.jpg',quality=95)
    print(json.dumps({key:verification[key] for key in ['status','frames_compared','changed_frames','added_track_frame_observations','detector_and_tracker_records_unchanged']},ensure_ascii=False))


if __name__=='__main__':
    main()
