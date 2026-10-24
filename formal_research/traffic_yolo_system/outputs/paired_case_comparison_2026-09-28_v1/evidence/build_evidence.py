#!/usr/bin/env python3
"""Assemble traceable scientific evidence panels from existing outputs; no inference.

Run with the bundled Python (Pillow), from any working directory.
All writes are restricted to this script's directory.
"""
from pathlib import Path
import csv
import hashlib
import json
import re
import shutil
import subprocess
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
BASE = OUT.parents[1]
OLD = BASE / 'five_intersections_2026-09-27_r2'
NEW = BASE / 'supplementary_five_2026-09-28_v3_repair'
NEWPORT = BASE / 'newport_full_v5_2026-09-24'
ROOT = BASE.parents[2]
FRAMES = OUT / 'selected_frames'
FRAMES.mkdir(exist_ok=True)
CAMERAS = dict(Newport='Bellevue_150th_Newport', NE12th='Bellevue_116th_NE12th',
               Eastgate='Bellevue_150th_Eastgate', SE38th='Bellevue_150th_SE38th',
               NE8th='Bellevue_Bellevue_NE8th')
INPUTS = {}
CHECKS = []

def digest(path):
    path = Path(path)
    if str(path) not in INPUTS:
        h = hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
                h.update(chunk)
        INPUTS[str(path)] = {'sha256': h.hexdigest(), 'bytes': path.stat().st_size}
    return INPUTS[str(path)]['sha256']

def read(path):
    digest(path)
    return json.loads(Path(path).read_text())

def check(name, actual, expected):
    ok = actual == expected
    CHECKS.append({'check': name, 'passed': ok, 'actual': actual, 'expected': expected})
    if not ok:
        raise ValueError(name)

old_sel = {r['camera_id']: r for r in read(OLD/'selection/selected_sources.json')['entries']}
new_sel = {r['camera_id']: r for r in read(NEW/'selection/selected_sources.json')['entries']}
old_idx = {r['intersection']: r for r in read(OLD/'five_video_results_index.json')['results']}
new_idx = {r['camera']: r for r in read(NEW/'supplementary_results_index.json')['results']}

def existing_contact(short, camera, batch, source, idx):
    receipt_path = batch/'delivery'/camera/'summary_archive/contacts/contact_receipt.json'
    receipt = read(receipt_path)
    check(f'{short} {batch.name} source identity', receipt['source_id'], source['source_id'])
    frame = receipt['frames'][0]  # Fixed existing early context frame, not output-selected.
    frame_path = receipt_path.parent/frame['path']
    check(f'{short} {batch.name} contact image SHA256', digest(frame_path), frame['sha256'])
    role = 'original' if batch == OLD else 'supplementary'
    local = FRAMES/f'{short}_{role}.png'
    shutil.copyfile(frame_path, local)
    result = dict(role=role, source_id=source['source_id'], split=source['split'],
                  source_duration_seconds=idx['duration_seconds'],
                  source_sha256=source['source_sha256'],
                  source_timestamp_seconds=frame['source_timestamp_seconds'],
                  frame_index=frame['frame_index'], image_path=str(local),
                  source_image_path=str(frame_path), image_sha256=digest(frame_path),
                  evidence_receipt_path=str(receipt_path), evidence_receipt_sha256=digest(receipt_path),
                  method='Copy existing contact frame without changing pixels or overlays')
    if batch == NEW:
        preview_path = batch/'delivery'/camera/'preview_receipt.json'
        preview = read(preview_path)
        result.update(preview_path=str(batch/'delivery'/camera/'preview.mp4'),
                      preview_receipt_path=str(preview_path),
                      preview_source_intervals=preview['selected_intervals'])
        check(f'{short} preview source identity', preview['source_id'], source['source_id'])
        check(f'{short} preview first frame mapping',
              preview['frame_mapping'][0]['source_frame_index'], frame['frame_index'])
    return result

def newport_original():
    source = old_sel[CAMERAS['Newport']]
    video = Path(old_idx['Newport']['video'])
    receipt_path = NEWPORT/'downloaded_server_results/presentation/render_receipt.json'
    receipt = read(receipt_path)
    check('Newport original annotated video SHA256', digest(video), receipt['output_sha256'])
    check('Newport original source identity', receipt['source_id'], source['source_id'])
    counts = NEWPORT/'downloaded_server_results/analysis/server_results/frame_counts.csv'
    digest(counts)
    with counts.open(encoding='utf-8-sig') as f:
        row = next(r for r in csv.DictReader(f) if float(r['source_timestamp_seconds']) >= 60)
    frame_path = FRAMES/'Newport_original.png'
    ffmpeg = ROOT/'formal_research/traffic_yolo_system/tools/full_video/ffmpeg_7_1_arm64/ffmpeg'
    argv = [str(ffmpeg), '-hide_banner', '-loglevel', 'info', '-threads', '1', '-y',
            '-copyts', '-ss', '60', '-i', str(video), '-map', '0:v:0', '-frames:v', '1',
            '-vf', 'showinfo', '-fps_mode', 'passthrough', '-update', '1', str(frame_path)]
    run = subprocess.run(argv, capture_output=True, text=True, check=True)
    (OUT/'Newport_extraction.log').write_text(run.stderr)
    matches = re.findall(r'n:\s*0 pts:\s*(\d+) pts_time:([\d.]+)', run.stderr)
    if len(matches) != 1:
        raise ValueError('Expected exactly one extracted frame timestamp')
    ticks = int(matches[0][0])
    check('Newport extracted PTS matches frame_counts', ticks,
          round(float(row['source_timestamp_seconds'])*90000))
    return dict(role='original', source_id=source['source_id'], split=source['split'],
                source_duration_seconds=old_idx['Newport']['duration_seconds'],
                source_sha256=source['source_sha256'], source_timestamp_seconds=ticks/90000,
                source_pts_ticks=ticks, time_base='1/90000', frame_index=int(row['frame_index']),
                image_path=str(frame_path), image_sha256=digest(frame_path),
                annotated_video_path=str(video), annotated_video_sha256=digest(video),
                evidence_receipt_path=str(receipt_path), evidence_receipt_sha256=digest(receipt_path),
                frame_counts_path=str(counts), extraction_command=argv,
                method='Decode one frame from verified existing full annotated video; original PTS preserved')

def roi_signature(path):
    d = read(path)
    return {'dimensions':[d['frame_width'],d['frame_height']],
            'assignment_point':d['assignment_point'],
            'ordered_zones':[(z['zone_name'], z['polygon_points']) for z in d['zones']]}

FONT_DIR = Path('/System/Library/Fonts/Supplemental')
FONT = FONT_DIR/'Arial.ttf'
BOLD = FONT_DIR/'Arial Bold.ttf'
def font(size, bold=False):
    return ImageFont.truetype(str(BOLD if bold else FONT), size)

def text(draw, xy, txt, size=28, bold=False, fill='#15253D'):
    draw.text(xy, txt, font=font(size,bold), fill=fill)

def make_panel(short, entries):
    canvas = Image.new('RGB', (2660, 1190), '#F5F7FB')
    draw = ImageDraw.Draw(canvas)
    text(draw,(35,22),f'{short}: illustrative original and supplementary context',40,True)
    for j, e in enumerate(entries):
        x = 35 + 1310*j
        label = 'Original complete case' if j == 0 else 'Supplementary source'
        text(draw,(x,86),f"{label} | {e['split']} | full source {e['source_duration_seconds']:.3f} s",28,True)
        text(draw,(x,126),e['source_id'],25)
        text(draw,(x,166),f"Original source PTS: {e['source_timestamp_seconds']:.6f} s | Frame {e['frame_index']}",27)
        im = Image.open(e['image_path']).convert('RGB')
        check(f'{short} {e["role"]} image dimensions', list(im.size), [1280,870])
        canvas.paste(im,(x,210))
    text(draw,(35,1100),'Existing overlays: colored polygons = selected image-space regions; boxes = model observations.',26)
    text(draw,(35,1140),'Illustrative snapshots, not representative of whole sources. Separate recordings; no synchronized signal phases.',26)
    canvas.save(OUT/f'{short}_pair.png')
    return canvas

pairs = []
panels = []
for short,camera in CAMERAS.items():
    old = (newport_original() if short == 'Newport' else
           existing_contact(short,camera,OLD,old_sel[camera],old_idx[short]))
    new = existing_contact(short,camera,NEW,new_sel[camera],new_idx[camera])
    old_roi = (NEWPORT/'config/roi.json' if short == 'Newport' else OLD/'config'/camera/'roi.json')
    new_roi = NEW/'config'/camera/'roi.json'
    a,b = roi_signature(old_roi),roi_signature(new_roi)
    if short != 'Newport':
        digest(OLD/'config'/camera/'applicability.json')
    digest(NEW/'config'/camera/'applicability.json')
    check(f'{short} ROI coordinates, order, dimensions and anchor unchanged', a, b)
    pair = {'intersection':short,'camera':camera,'entries':[old,new],
            'original_roi_path':str(old_roi),'supplementary_roi_path':str(new_roi),
            'roi_geometry_identical':a==b,'figure_path':str(OUT/f'{short}_pair.png')}
    pairs.append(pair)
    panels.append(make_panel(short,[old,new]))

overview = Image.new('RGB',(1596,714*5),'white')
for i,panel in enumerate(panels):
    overview.paste(panel.resize((1596,714),Image.Resampling.LANCZOS),(0,i*714))
overview.save(OUT/'five_pairs_overview.jpg',quality=94)

light_evidence = []
for short in ['Eastgate','SE38th']:
    camera = CAMERAS[short]
    folder = NEW/'source_review'/camera
    manifest_path = folder/'sample_manifest.json'
    manifest = read(manifest_path)
    canvas = Image.new('RGB',(2660,985),'#F5F7FB')
    draw=ImageDraw.Draw(canvas)
    text(draw,(35,22),f'{short}: lighting changes within the supplementary source',40,True)
    text(draw,(35,84),f"{new_sel[camera]['source_id']} | Validation | full source {new_idx[camera]['duration_seconds']:.3f} s",30)
    entries=[]
    for j,k in enumerate([0,5]):
        sample=manifest['samples'][k]
        path=folder/f'sample_{k:02d}.png'
        check(f'{short} source sample {k} SHA256',digest(path),sample['sha256'])
        local=FRAMES/f'{short}_supplementary_raw_{k:02d}.png'
        shutil.copyfile(path,local)
        x=35+1310*j
        text(draw,(x,135),f"{'Start' if j==0 else 'End context'} | Original source PTS: {sample['first_output_frame_pts_seconds']:.6f} s",28,True)
        canvas.paste(Image.open(path).convert('RGB'),(x,188))
        entries.append(dict(source_image_path=str(path),local_image_path=str(local),
                            source_timestamp_seconds=sample['first_output_frame_pts_seconds'],
                            image_sha256=digest(path),manifest_path=str(manifest_path)))
    text(draw,(35,935),'Original source images; no new inference. Illumination changes can affect detectability as well as observed traffic.',26)
    canvas.save(OUT/f'{short}_light_change.png')
    light_evidence.append({'intersection':short,'source_id':new_sel[camera]['source_id'],'split':'Validation',
                           'figure_path':str(OUT/f'{short}_light_change.png'),'entries':entries})

def extract_eastgate_context():
    camera = CAMERAS['Eastgate']
    source = new_sel[camera]
    source_path = Path(source['source_path'])
    check('Eastgate supplementary original video SHA256', digest(source_path), source['source_sha256'])
    counts_path = NEW/'delivery'/camera/'summary_archive/output/frame_counts.csv'
    digest(counts_path)
    with counts_path.open(encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    entries=[]
    canvas=Image.new('RGB',(2660,1035),'#F5F7FB')
    draw=ImageDraw.Draw(canvas)
    text(draw,(35,22),'Eastgate: original-image context for early and later analysis intervals',39,True)
    text(draw,(35,82),f"{source['source_id']} | Validation | full source {new_idx[camera]['duration_seconds']:.3f} s",29)
    for j,target in enumerate([240,2770]):
        frame_path = FRAMES/f'Eastgate_supplementary_raw_{target}s.png'
        ffmpeg=ROOT/'formal_research/traffic_yolo_system/tools/full_video/ffmpeg_7_1_arm64/ffmpeg'
        argv=[str(ffmpeg),'-hide_banner','-loglevel','info','-threads','1','-y','-copyts','-ss',str(target),
              '-i',str(source_path),'-map','0:v:0','-frames:v','1','-vf','showinfo',
              '-fps_mode','passthrough','-update','1',str(frame_path)]
        run=subprocess.run(argv,capture_output=True,text=True,check=True)
        (OUT/f'Eastgate_{target}s_extraction.log').write_text(run.stderr)
        ticks=int(re.findall(r'n:\s*0 pts:\s*(\d+) pts_time:',run.stderr)[0])
        row=next(r for r in rows if round(float(r['source_timestamp_seconds'])*90000)==ticks)
        check(f'Eastgate {target} s original PTS matches frame_counts',ticks,
              round(float(row['source_timestamp_seconds'])*90000))
        x=35+1310*j
        text(draw,(x,136),f"Original source PTS: {ticks/90000:.6f} s | Frame {row['frame_index']}",28,True)
        canvas.paste(Image.open(frame_path).convert('RGB'),(x,188))
        entries.append(dict(source_timestamp_seconds=ticks/90000,source_pts_ticks=ticks,time_base='1/90000',
                            frame_index=int(row['frame_index']),image_path=str(frame_path),
                            image_sha256=digest(frame_path),extraction_command=argv,
                            matched_frame_counts=row))
    text(draw,(35,935),'Original source frames without added detections. Chosen as context for existing quantitative analysis intervals.',26)
    text(draw,(35,979),'A still image does not establish interval-average traffic; changing illumination can also affect detectability.',26)
    canvas.save(OUT/'Eastgate_analysis_interval_context.png')
    return dict(intersection='Eastgate',source_id=source['source_id'],source_path=str(source_path),
                source_sha256=digest(source_path),split='Validation',
                selected_target_seconds=[240,2770],
                selection_basis='Root quantitative analyst requested early and late raw-image contexts; no new model inference.',
                figure_path=str(OUT/'Eastgate_analysis_interval_context.png'),
                frame_counts_path=str(counts_path),entries=entries)

eastgate_context=extract_eastgate_context()
for p in [NEWPORT/'config/background_policy.json',
          NEW/'config/Bellevue_150th_Newport/background_policy.json',
          NEW/'config/Bellevue_150th_Newport/background_applicability.json',
          OLD/'五路口完整结果与说明.md', NEW/'补充批次完整结果与说明.md']:
    digest(p)

metadata = {'schema':'paired_visual_evidence_v1',
            'selection_rule':'First existing short-check contact frame near original source PTS 60 s; Newport original first rendered frame at or after 60 s. Chosen for consistent early context, not for numerical extremity or whole-source representativeness.',
            'time_convention':'All displayed PTS values are original-source normalized timestamps in seconds, not preview playback seconds. Recordings are separate and are not synchronized signal cycles.',
            'no_new_inference':True,'no_new_manual_labels':True,'no_main_test_media_read':True,
            'pixel_policy':'Individual paired panels paste existing annotated images at native size. The overview is resized only for navigation. Raw lighting panels contain original existing source samples at native size. No brightness, contrast or geometry changes.',
            'pairs':pairs,'within_source_light_evidence':light_evidence,
            'eastgate_analysis_interval_context':eastgate_context,
            'input_hashes':INPUTS,'checks':CHECKS,'all_checks_passed':all(c['passed'] for c in CHECKS)}
(OUT/'evidence_metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
print(json.dumps({'pairs':len(pairs),'additional_light_panels':len(light_evidence),
                  'checks':len(CHECKS),'all_passed':metadata['all_checks_passed'],'output':str(OUT)}))
