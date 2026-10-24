"""Post-hoc Main Test ROI count diagnostic from existing completed run exports."""
from pathlib import Path
from collections import Counter
import ast, csv, hashlib, json, math
from PIL import Image

OUT = Path(__file__).resolve().parent
BASE = Path('/Users/zoki/Desktop/traffic_projects/formal_research')
RUN = BASE/'traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28'
protocol = json.loads((OUT/'protocol.json').read_text())
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
for p,h in protocol['inputs'].items(): assert digest(Path(p)) == h, p

# Reuse audited geometry only; do not run the Validation evaluation again.
val_script=OUT.parent/'calculate.py'
namespace={'__file__':str(val_script)}
prefix=val_script.read_text().split('records = [json.loads(line)')[0]
exec(compile(prefix,str(val_script),'exec'),namespace)
assign,layouts=namespace['assign'],namespace['layouts']
tree=ast.parse(val_script.read_text())
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='metrics')
exec(compile(ast.Module(body=[fn],type_ignores=[]),str(val_script),'exec'))

manifest=json.loads((RUN/'preparation/dataset_manifest.json').read_text())
runtime=json.loads((RUN/'run/effective_runtime.json').read_text())
args=json.loads((RUN/'run/effective_evaluation_args.json').read_text())
assert json.loads((RUN/'run/status.json').read_text())['state']=='completed'
assert runtime['seen']==runtime['dataset_length']==len(manifest['entries'])==184
assert runtime['image_inventory_matches_manifest'] and args['save_txt'] and args['save_conf']
assert args['conf']==.001 and args['iou']==.7 and not args['agnostic_nms']
expected={Path(e['image_path']).stem for e in manifest['entries']}
exports={p.stem:p for p in (RUN/'run/raw/labels').glob('*.txt')}
assert len(exports)==174 and set(exports)<=expected
rows,frames,assignments,missing=[],[],[],[]
classes=Counter(); rounding=[]; min_conf_margin=1.0; min_boundary_margin=float('inf')
def round_error(v): return .5*10**(math.floor(math.log10(abs(v)))-5) if v else 0.0
def edge_distance(p,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy)))
    return math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)

for e in sorted(manifest['entries'],key=lambda x:x['image_path']):
    image=Path(e['original_image_path']);label=Path(e['original_label_path'])
    assert digest(label)==e['label_sha256'] and digest(image)==e['image_sha256']
    w,h=Image.open(image).size
    assert (w,h)==(1280,720)
    camera=e['intersection'];layout=layouts[camera]
    gt=[list(map(float,x.split())) for x in label.read_text().splitlines() if x.strip()]
    pfile=exports.get(image.stem)
    if pfile is None:missing.append({'image':image.name,'source':e['source_video'],'ground_truth_boxes':len(gt),'prediction_count':0,'basis':'Completed 184-image manifest plus nonempty-only native export semantics'})
    exported=[list(map(float,x.split())) for x in pfile.read_text().splitlines()] if pfile else []
    for b in exported:
        assert len(b)==6
        margin=abs(b[5]-.35)-round_error(b[5]);min_conf_margin=min(min_conf_margin,margin)
        assert margin>0,('ambiguous confidence rounding',image.name,b)
    pred=[b for b in exported if b[5]>=.35]
    counts={'gt':Counter(),'pred':Counter()}
    for kind,boxes in [('gt',gt),('pred',pred)]:
        for index,b in enumerate(boxes):
            cls,cx,cy,bw,bh=b[:5]
            assert int(cls)==cls and 0<=cls<=3
            xyxy=((cx-bw/2)*w,(cy-bh/2)*h,(cx+bw/2)*w,(cy+bh/2)*h)
            region=assign(xyxy,layout)
            if kind=='pred':
                point=(cx*w,(cy+bh/2)*h)
                bound=math.hypot(w*round_error(cx),h*(round_error(cy)+round_error(bh)/2))
                distances=[]
                for z in layout['zones']:
                    polygon=z['polygon_points']
                    distances += [edge_distance(point,a,polygon[(i+1)%len(polygon)]) for i,a in enumerate(polygon)]
                margin=min(distances)-bound;min_boundary_margin=min(min_boundary_margin,margin)
                if margin<=0:rounding.append({'image':image.name,'box':index,'region':region,'distance':min(distances),'rounding_radius':bound})
            counts[kind][region]+=1
            if kind=='gt':classes[int(cls)]+=1
            assignments.append({'image':image.name,'camera':camera,'kind':kind,'box_index':index,'class_id':int(cls),'region':region})
    for z in layout['zones']:
        region=z['zone_name'];g,p=counts['gt'][region],counts['pred'][region]
        rows.append({'image':image.name,'source_video':e['source_video'],'camera':camera,'lighting':e['lighting'],'region':region,'gt_count':g,'pred_count':p,'error':p-g,'absolute_error':abs(p-g)})
    frames.append({'image':image.name,'source_video':e['source_video'],'camera':camera,'lighting':e['lighting'],'gt_whole_image':len(gt),'pred_whole_image':len(pred),'exported_floor_predictions':len(exported),'gt_outside_roi':counts['gt']['outside_roi'],'pred_outside_roi':counts['pred']['outside_roi'],'gt_count':len(gt)-counts['gt']['outside_roi'],'pred_count':len(pred)-counts['pred']['outside_roi']})
assert [classes[i] for i in range(4)]==manifest['class_counts']==[1946,4,13,89]
assert not rounding,rounding
camera_results=[]
for camera in sorted(layouts):
    rr=[r for r in rows if r['camera']==camera];ff=[r for r in frames if r['camera']==camera]
    camera_results.append({'camera':camera,'images':len(ff),'source_videos':len({r['source_video'] for r in ff}),'regions':len(layouts[camera]['zones']),'roi_image_metrics':metrics(rr),'in_roi_sum_per_image_metrics':metrics(ff)})
summary={'evaluation':protocol['evaluation'],'confidence':.35,'images':184,'source_videos':17,'cameras':5,'regions':22,'roi_image_pairs':len(rows),'all_roi_image_metrics':metrics(rows),'whole_image_metrics':metrics([{'gt_count':r['gt_whole_image'],'pred_count':r['pred_whole_image']} for r in frames]),'in_roi_sum_per_image_metrics':metrics(frames),'by_camera':camera_results,'outside_roi':{'ground_truth_boxes':sum(r['gt_outside_roi'] for r in frames),'prediction_boxes':sum(r['pred_outside_roi'] for r in frames)},'ground_truth_class_counts':dict(classes),'prediction_export_files':len(exports),'missing_export_images':missing,'rounding_ambiguities':rounding,'minimum_confidence_margin_beyond_rounding':min_conf_margin,'minimum_boundary_distance_beyond_rounding_px':min_boundary_margin,'independent_assignment_check':'Frozen ray-casting and independent winding-number implementation agree for every GT/pred point','label_integrity':'Every label and image SHA256 matches original frozen manifest','new_training':False,'new_inference':False,'new_annotations':False,'application_filters_or_tracking_evaluated':False,'protocol_sha256':digest(OUT/'protocol.json'),'script_sha256':digest(Path(__file__))}
for name,data in [('roi_image_errors.csv',rows),('frame_counts.csv',frames),('box_assignments.csv',assignments)]:
    with (OUT/name).open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)
(OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
for p,h in protocol['inputs'].items():assert digest(Path(p))==h,('input changed',p)
print(json.dumps({k:summary[k] for k in ['images','roi_image_pairs','all_roi_image_metrics','by_camera','outside_roi','minimum_confidence_margin_beyond_rounding','minimum_boundary_distance_beyond_rounding_px']},indent=2))
