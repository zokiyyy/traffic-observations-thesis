"""Prepare unpredicted source images for a fixed Validation count review."""
from pathlib import Path
import hashlib
import json
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / 'outputs/video_pipeline/yolo11s_newport_val_120s_v1_2026-09-23'
OUT = PILOT / 'count_review_2026-09-24'
TIMES = [15, 45, 65, 85, 95, 115]
CROPS = {'top': (490, 0, 790, 175), 'right': (750, 35, 1050, 235),
         'left': (0, 200, 420, 540)}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    manifest = json.loads((PILOT / 'input/clip_manifest.json').read_text())
    zones = json.loads((PILOT / 'input/zones.json').read_text())
    assert manifest['source_split']['Formal Split'] == 'Validation'
    video = Path(manifest['clip_file'])
    assert sha(video) == manifest['clip_sha256']
    OUT.mkdir(exist_ok=True)
    blind = OUT / 'blind'
    blind.mkdir(exist_ok=False)
    protocol = {
        'sample_times_seconds': TIMES, 'frame_indices': [t*30 for t in TIMES],
        'selection': 'New fixed times, chosen before inspecting model counts; 85s included to inspect distant dense traffic. Development sample, not randomly sampled final evaluation.',
        'source_split': 'Validation', 'video': str(video),
        'clip_sha256': manifest['clip_sha256'], 'zones_sha256': sha(PILOT/'input/zones.json'),
        'source_offset_seconds': 1600, 'fps': 30, 'width': 1280, 'height': 720,
        'reviewer': 'AI assistant visual inspection; not user human ground truth',
        'rule': 'Count visible physical vehicles with estimated full visible-vehicle bounding-box bottom centre inside fixed polygon. Border uncertainty and occluded/distant ambiguity must be marked, not forced into exact count. Outside polygons excluded.',
        'blind_rule': 'Review source-only images before opening predictions; record per-zone exact count only when supportable, otherwise min/max/uncertain. Save initial observations before comparison.',
        'neighbor_frame_offsets': [-15, 0, 15],
        'crop_xyxy': CROPS, 'zones': zones['zones'],
        'unchanged': ['YOLO weights', 'tracking parameters', 'ROI polygons', 'existing outputs'],
    }
    (OUT/'review_protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2)+'\n')
    cap = cv2.VideoCapture(str(video))
    assert cap.isOpened()
    selected = {t*30+delta for t in TIMES for delta in [-15,0,15]}
    saved = {}
    i=0
    while True:
        ok, frame=cap.read()
        if not ok: break
        if i in selected:
            saved[i]=frame
        i+=1
    cap.release()
    assert i==3600 and len(saved)==len(selected)
    for t in TIMES:
        frame=saved[t*30]
        assert cv2.imwrite(str(blind/f't{t:03d}_clean.jpg'),frame,[cv2.IMWRITE_JPEG_QUALITY,98])
        roi=frame.copy()
        for z in zones['zones']:
            cv2.polylines(roi,[np.array(z['polygon_points'],np.int32)],True,z['color_bgr'],2,cv2.LINE_AA)
        assert cv2.imwrite(str(blind/f't{t:03d}_roi.jpg'),roi,[cv2.IMWRITE_JPEG_QUALITY,98])
        for name,(x1,y1,x2,y2) in CROPS.items():
            for kind,img in [('clean',frame),('roi',roi)]:
                crop=cv2.resize(img[y1:y2,x1:x2],None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
                assert cv2.imwrite(str(blind/f't{t:03d}_{name}_{kind}.jpg'),crop,[cv2.IMWRITE_JPEG_QUALITY,98])
        # Unpredicted temporal neighbours help distinguish vehicles from background.
        x1,y1,x2,y2=CROPS['top']
        tiles=[]
        for delta in [-15,0,15]:
            crop=cv2.resize(saved[t*30+delta][y1:y2,x1:x2],None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC)
            panel=np.full((32,crop.shape[1],3),245,np.uint8)
            cv2.putText(panel,f'Source only | clip {t+delta/30:.2f}s',(8,22),cv2.FONT_HERSHEY_SIMPLEX,.55,(30,30,30),1,cv2.LINE_AA)
            tiles.append(np.vstack([panel,crop]))
        assert cv2.imwrite(str(blind/f't{t:03d}_top_sequence.jpg'),np.hstack(tiles),[cv2.IMWRITE_JPEG_QUALITY,98])
    print(json.dumps({'prepared':str(OUT),'times':TIMES,'source_frames_verified':i,'source_images_only':True}))

if __name__ == '__main__':
    main()
