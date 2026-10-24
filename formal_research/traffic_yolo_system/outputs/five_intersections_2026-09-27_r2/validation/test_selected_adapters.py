"""Synthetic software checks only; no model, original dataset media, or accuracy claims."""
import copy
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(HERE));sys.path.insert(0,str(ROOT/'src'))
from selection_binding import selected_entry,bind_prepared,NEW_SOURCES,MODEL_SHA
import audit_selected_output as audit
import prepare_selected_time_audit as native
from full_video.streaming import StreamWriter
spec=importlib.util.spec_from_file_location('selected_renderer',HERE.parent/'presentation/render_selected_pts.py')
renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)


def dump(path,value):path.write_text(json.dumps(value),encoding='utf-8')
def lines(path,values):path.write_text(''.join(json.dumps(v)+'\n' for v in values))
def receipt(attempt,source_id):
    files={p.relative_to(attempt).as_posix():{'bytes':p.stat().st_size,'sha256':audit.sha(p)} for p in attempt.rglob('*') if p.is_file() and p.name not in ('status.json','completion.json')}
    dump(attempt/'completion.json',{'source_id':source_id,'fingerprint':'fixture','files':files})


def fixture(root,nzones=3,phase='full'):
    camera_id,source_id=next(iter(NEW_SOURCES.items()))
    entry={'camera_id':camera_id,'source_id':source_id,'source_sha256':'a'*64,'split':'Validation','action':'new','short_check_seconds':[.2,12.3]}
    selection=root/'selection.json';dump(selection,{'entries':[entry]})
    layout={'camera_id':camera_id,'frame_width':200,'frame_height':120,'assignment_point':'bottom_center','boundary_tie_break':'zone_order','zones':[
        {'zone_name':f'road_{i}','polygon_points':[[i*200/nzones,0],[min((i+1)*200/nzones,199),0],[min((i+1)*200/nzones,199),119],[i*200/nzones,119]]} for i in range(nzones)]}
    pts=[100,125,175,1325];durations=[25,50,1150,10]
    clocks=[{'frame_index':i,'pkt_pts':pts[i],'pkt_duration':durations[i],'width':200,'height':120} for i in range(4)]
    timing=root/'timing.jsonl';lines(timing,clocks)
    begin,end=(0,4) if phase=='full' else (1,3)
    start=(pts[begin]-100)/100;stop=12.35 if end==4 else (pts[end]-100)/100
    preflight={'input_sha256':'a'*64,'input_frame_count':4,'frame_count':end-begin,'first_frame_index':begin,'end_frame_index_exclusive':end,
       'source_start_seconds':start,'source_end_seconds':stop,'time_mode':'audited_pts','frame_timing_sha256':audit.sha(timing),'frame_timing_path':str(timing),
       'time_base_num':1,'time_base_den':100,'input_first_pts_ticks':100,'input_last_pts_ticks':1325,'final_frame_duration_ticks':10,'input_duration_ticks':1235,'input_duration_seconds':12.35,'width':200,'height':120}
    config={'model':{'sha256':MODEL_SHA},'detector':{'conf':.1,'imgsz':640,'iou':.7,'half':False,'agnostic_nms':False,'max_det':300,'augment':False,'rect':True,'batch':1,'classes':None},
       'tracker':{'tracker_type':'bytetrack','track_high_thresh':.35,'track_low_thresh':.1,'new_track_thresh':.35,'track_buffer':30,'match_thresh':.8,'fuse_score':True},
       'dedup':{'mode':'cross_class_greedy_iou','iou_threshold':.7,'containment_policy_sha256':audit.CONTAINMENT_SHA,'containment_policy':dict(audit.CONTAINMENT_FIELDS)},
       'aggregation':{'window_seconds':10},'input':{'kind':'video'},'segment':None if phase=='full' else {'start_seconds':start,'end_seconds':stop}}
    camera={'camera_id':camera_id,'source_ids':[source_id],'layout':layout,'layout_identity':{'camera_id':camera_id},'background_filter':{'mode':'none','reason':'No camera-specific evidence'}}
    metadata={'source':'synthetic fixture only, not research evidence','split':'Validation','run':'fixture','model_version':'fixture','roi_version':'fixture','rules_version':'fixture','configuration_sha256':'fixture'}
    prepared={'task':{k:entry[k] for k in ('source_id','camera_id','split')},'config':config,'camera':camera,'preflight':preflight,'metadata':metadata,'fingerprint':'fixture'}
    attempt=root/'attempt';attempt.mkdir()
    dump(attempt/'prepared.json',prepared);dump(attempt/'provenance.json',prepared);dump(attempt/'status.json',{'status':'completed'})
    raw=[{'class_id':0,'class_name':'car','confidence':.8,'box_xyxy':[5,5,15,15]}]
    writer=StreamWriter(attempt,metadata,layout,start,stop,chunk_frames=2)
    for index in range(begin,end):
        writer.add({'frame_index':index,'segment_frame_index':index-begin,'source_timestamp_seconds':(pts[index]-100)/100,'duration_seconds':durations[index]/100,'valid':True,
            'configuration_sha256':'fixture','quality':{'decoded':True,'inference_complete':True,'time_mode':'audited_pts','duration_semantics':'decoded_frame_presentation_interval','detector_max_det_reached':False},'decoder_position_msec':(pts[index]-100)*10,'decoder_first_position_msec':0,
            'pts':pts[index],'duration_ticks':durations[index],'time_base_num':1,'time_base_den':100,'decoded_frame_sha256':'c'*64,'raw_detections':raw,
            'duplicate_suppression':{'input_detection_indices':[0],'kept_detection_indices':[0],'removed':[]},
            'contained_vehicle_suppression':{'policy_sha256':audit.CONTAINMENT_SHA,'input_detection_indices':[0],'kept_detection_indices':[0],'removed':[],'unresolved_pairs':[]},
            'tracker_input_detection_indices':[0],'tracks_primary':[{**raw[0],'track_id':1,'detection_index':0}]})
    writer.finish();receipt(attempt,source_id)
    args=SimpleNamespace(attempt=attempt,output=root/'qc',selection=selection,source_id=source_id,pts=timing,phase=phase,layout=None,plot=True)
    return args,prepared,entry


class AdapterTests(unittest.TestCase):
    def test_scope_rejects_newport_and_test_before_opening_selection(self):
        for name in ['Main_Test.mp4','Bellevue_150th_Newport__2017-09-11_08-08-31.mp4']:
            with self.assertRaisesRegex(ValueError,'outside'):selected_entry('/definitely/not/read',name)

    def test_selection_camera_split_hash_and_reuse_binding(self):
        with tempfile.TemporaryDirectory() as d:
            args,prepared,entry=fixture(Path(d))
            self.assertEqual(selected_entry(args.selection,args.source_id),entry)
            for field,value in [('split','Main Test'),('action','reuse'),('camera_id','other'),('source_sha256','missing')]:
                bad={**entry,field:value};dump(args.selection,{'entries':[bad]})
                with self.assertRaises(ValueError):selected_entry(args.selection,args.source_id)
            for field,value in [('input_sha256','b'*64)]:
                bad=copy.deepcopy(prepared);bad['preflight'][field]=value
                with self.assertRaises(ValueError):bind_prepared(entry,bad)
            bad=copy.deepcopy(prepared);bad['camera']['layout_identity']['camera_id']='Newport'
            with self.assertRaises(ValueError):bind_prepared(entry,bad)

    def test_full_actual_writer_schema_arbitrary_zone_counts(self):
        for count in (2,3,4,5):
            with self.subTest(zones=count),tempfile.TemporaryDirectory() as d:
                args,prepared,entry=fixture(Path(d),count)
                report=audit.audit_run(args)
                self.assertEqual(report['status'],'passed',report['error_examples'])
                self.assertEqual(report['error_count'],0)
                self.assertEqual(len(report['zones']),count)
                self.assertEqual(report['zones']['road_0']['mean_current_vehicles'],1)
                self.assertAlmostEqual(report['tail_window_seconds'],2.35)
                self.assertEqual(report['flags']['source_long_presentation_interval'],1)
                self.assertTrue((args.output/'current_counts_over_source.png').is_file())

    def test_short_original_index_and_relative_time_with_full_tail_scan(self):
        with tempfile.TemporaryDirectory() as d:
            args,prepared,entry=fixture(Path(d),phase='short')
            report=audit.audit_run(args)
            self.assertEqual(report['status'],'passed',report['error_examples'])
            self.assertEqual(report['source_frame_range'],[1,3])
            self.assertEqual(report['selected_source_time_range_seconds'],[.25,12.25])
            all_rows=list(audit.rows(args.pts));all_rows[-1]['pkt_duration']=0;lines(args.pts,all_rows)
            with self.assertRaisesRegex(ValueError,'positive'):
                list(audit.selected_timing(args.pts,prepared['preflight'],entry,'short'))

    def test_short_bounds_require_predeclared_ceil_start_floor_end(self):
        with tempfile.TemporaryDirectory() as d:
            args,prepared,entry=fixture(Path(d),phase='short')
            entry['short_check_seconds']=[0,12.3]
            with self.assertRaisesRegex(ValueError,'predeclared'):
                list(audit.selected_timing(args.pts,prepared['preflight'],entry,'short'))

    def test_pkt_pts_alias_and_zero_missing_conflicting_duration(self):
        row={'frame_index':0,'pkt_pts':100,'pkt_duration':1,'width':200,'height':120}
        actual=audit.canonical_timing(row,100,Fraction(1,90000))
        self.assertEqual(actual['pts'],100);self.assertEqual(actual['duration_ticks'],1)
        for bad in [{**row,'pkt_duration':0},{k:v for k,v in row.items() if k!='pkt_duration'},
                    {**row,'duration':2},{**row,'pts':101},{**row,'original_pts_seconds':9}]:
            with self.assertRaises(ValueError):audit.canonical_timing(bad,100,Fraction(1,90000))
        for bad in ['duration=1','pts=1|pkt_pts=2|duration=1','pkt_dts=100|duration=1']:
            with self.assertRaises(ValueError):native.parse_row(bad)
        self.assertEqual(native.parse_row('pkt_pts=100|pkt_duration=1')['pts'],100)

    def test_none_background_retains_walllike_car_but_still_deduplicates(self):
        raw=[{'class_id':0,'confidence':.8,'box_xyxy':[1,1,100,100]}, {'class_id':3,'confidence':.5,'box_xyxy':[1,1,100,100]}]
        result=audit.expected_filters(raw,[],None)
        self.assertEqual(result[0],[0,1]);self.assertEqual(result[2],[])
        self.assertEqual(result[3],[0]);self.assertEqual(result[4],[(1,0)])

    def test_renderer_exact_time_and_dynamic_labels_keep_source_pixels(self):
        import cv2,numpy as np
        sys.path.insert(0,str(ROOT/'src'))
        from render_video_tracking_pilot import text
        for count in (2,3,4,7):
            zones={'zones':[{'zone_name':'road_'+str(i),'display_name':'camera approach '+str(i),'_color':(200,100,50)} for i in range(count)]}
            row={'frame_index':0,'source_timestamp_seconds':0,'duration_seconds':11.7,'zone_counts':{'road_'+str(i):i for i in range(count)},'total_in_zones':count,'outside_zone_count':2}
            canvas=np.zeros((870,1280,3),dtype=np.uint8);canvas[150:]=77
            renderer.draw_panel(canvas,row,zones,'selected_camera',cv2,text)
            self.assertTrue((canvas[150:]==77).all())
            self.assertTrue(canvas[:150].any())
        preflight={'time_base_num':1,'time_base_den':90000,'input_first_pts_ticks':10}
        row={'frame_index':0,'valid':True,'pts':10,'duration_ticks':1056025,'time_base_num':1,'time_base_den':90000,'source_timestamp_seconds':0}
        self.assertEqual(renderer.record_time(row,0,preflight)[:2],(0,1056025))
        for key,value in [('duration_ticks',0),('pts',11),('frame_index',1)]:
            with self.assertRaises(ValueError):renderer.record_time({**row,key:value},0,preflight)

    def test_qc_detects_silent_count_corruption_even_with_rebuilt_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            args,prepared,entry=fixture(Path(d))
            rr=list(audit.rows(args.attempt/'frames.jsonl'));rr[1]['zone_counts']['road_0']=3;lines(args.attempt/'frames.jsonl',rr)
            receipt(args.attempt,args.source_id)
            report=audit.audit_run(args)
            self.assertEqual(report['status'],'failed')
            self.assertGreater(report['error_counts']['frame_roi_counts'],0)

if __name__=='__main__':unittest.main()
