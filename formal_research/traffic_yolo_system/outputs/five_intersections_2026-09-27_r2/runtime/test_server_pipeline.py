"""Bounded runner tests use only synthetic bytes, never original media or SSH."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('bounded_pipeline', Path(__file__).with_name('server_pipeline.py'))
P = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(P)


class FakePipeline(P.Pipeline):
    def __init__(self, root, codes=None):
        super().__init__(root, {'selection': {'path': 'selection.json'}, 'inputs': []}, 'a'*64, 0)
        self.codes = list(codes or [0]); self.calls = 0; self.events = []
    def event(self, stage, **kw): self.events.append(stage)
    def run_child(self, argv, attempt, phase, **kw):
        self.calls += 1; code = self.codes.pop(0)
        P.atomic(attempt / 'execution.json', {'status': 'exited', 'returncode': code,
            'execution_wall_seconds': 1.25, 'started_at': 'start', 'ended_at': 'end'})
        if code == 0: (attempt / 'output.txt').write_text('verified exact result')
        return code


def finish(attempt):
    if (attempt / 'output.txt').read_text() != 'verified exact result': raise ValueError('Output invalid')
    return {'result': 'okay'}, [attempt / 'output.txt']


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def stage(self, pipeline, name='full', binding=None):
        return pipeline.stage('camera', name, binding or {'source': 'fixed'}, lambda a,retry: ['synthetic'], finish)
    def fixture_package(self):
        new = self.root / P.NEW_REL; (new / 'runtime').mkdir(parents=True)
        selection = {'selection_frozen': True, 'entries': []}
        sources = []
        for sid, (start, duration) in P.ALLOWED.items():
            camera = sid.split('__')[0]
            entry = dict(source_id=sid,camera_id=camera,split='Validation',source_sha256='b'*64,source_bytes=100,action='new')
            selection['entries'].append(entry)
            config = dict(detector=P.DETECTOR,tracker=P.TRACKER,model={'path':P.MODEL_PATH,'sha256':P.MODEL_SHA},
                input={'kind':'video','source_id':sid,'sha256':'b'*64},development_only=True,inference_enabled=True,segment=None,
                dedup={'mode':'cross_class_greedy_iou','iou_threshold':.7,'containment_policy_sha256':P.CONTAINMENT_SHA},
                time={'max_frame_duration_seconds':P.DURATION_LIMITS[camera]})
            config_path = Path('config') / (camera+'.json'); P.atomic(self.root/config_path,config)
            source={k:entry[k] for k in ('source_id','camera_id','split','source_sha256','source_bytes')}
            source.update(full_config={'path':str(config_path),'sha256':P.sha(self.root/config_path)},short_start_seconds=start,short_target_seconds=duration)
            sources.append(source)
        selection['entries'].append({'camera_id':'Bellevue_150th_Newport','action':'reuse'})
        P.atomic(self.root/'selection.json', selection); (self.root/'split.csv').write_text('frozen metadata')
        (self.root/'helper.py').write_text('pass\n')
        job = dict(schema_version='five_intersections_server_job_v1',job_id='five_intersections_20260927_r2',sources=sources,
            selection={'path':'selection.json','sha256':P.sha(self.root/'selection.json')},
            split_manifest={'path':'split.csv','sha256':P.sha(self.root/'split.csv')},
            inputs=[{'path':'helper.py','sha256':P.sha(self.root/'helper.py'),'roles':['runtime']}])
        return job
    def write_job(self, job):
        path=self.root/P.NEW_REL/'runtime/job_manifest.json'; P.atomic(path,job); return P.sha(path)
    def test_exact_fixed_selection_passes_without_opening_media(self):
        job=self.fixture_package(); P.verify_package(self.root,job,self.write_job(job),verify_media=False)
    def test_test_split_cannot_be_selected_even_with_updated_manifest_hash(self):
        job=self.fixture_package(); job['sources'][0]['split']='Main Test'
        with self.assertRaises(ValueError): P.verify_package(self.root,job,self.write_job(job),verify_media=False)
    def test_old_failed_daytime_source_cannot_reenter_r2(self):
        job=self.fixture_package()
        selected=next(row for row in job['sources'] if row['camera_id']=='Bellevue_116th_NE12th')
        selected['source_id']='Bellevue_116th_NE12th__2017-09-11_15-08-36.mp4'
        with self.assertRaises(ValueError):P.verify_package(self.root,job,self.write_job(job),verify_media=False)
    def test_newport_cannot_replace_one_approved_source(self):
        job=self.fixture_package(); job['sources'][0]['source_id']='Bellevue_150th_Newport__2017-09-11_08-08-31.mp4'
        with self.assertRaises(ValueError): P.verify_package(self.root,job,self.write_job(job),verify_media=False)
    def test_manifest_and_package_tamper_rejected(self):
        job=self.fixture_package(); digest=self.write_job(job)
        with self.assertRaises(ValueError): P.verify_package(self.root,job,'f'*64,verify_media=False)
        (self.root/'helper.py').write_text('changed')
        with self.assertRaises(ValueError): P.verify_package(self.root,job,digest,verify_media=False)
    def test_short_interval_cannot_expand(self):
        job=self.fixture_package(); job['sources'][0]['short_target_seconds']=120
        with self.assertRaises(ValueError): P.verify_package(self.root,job,self.write_job(job),verify_media=False)
    def test_path_traversal_absolute_and_symlinks_rejected(self):
        for value in ('../x','/tmp/x','x\\bad'):
            with self.assertRaises(ValueError): P.within(self.root,value)
        (self.root/'target').write_text('x'); (self.root/'link').symlink_to('target')
        with self.assertRaises(ValueError): P.within(self.root,'link')
    def test_original_pts_ceiling_start_floor_end_preserves_holds(self):
        timing=self.root/'timing.jsonl'
        timing.write_text(''.join(json.dumps({'frame_index':i,'pts':pts})+'\n' for i,pts in enumerate([100,195,202,217,387,399,501])))
        segment, receipt=P.exact_segment(timing,{'pts':{'time_base':'1/10','first_pts_ticks':100}},10,20)
        self.assertEqual(segment,{'start_seconds':10.2,'end_seconds':29.9})
        self.assertEqual(receipt['first_frame_index'],2); self.assertEqual(receipt['end_frame_index_exclusive'],5)
    def test_completed_stage_skips_only_intact_hash_bound_output(self):
        runner=FakePipeline(self.root); _,receipt=self.stage(runner); self.stage(runner)
        self.assertEqual(runner.calls,1)
        data=P.read(receipt); target=self.root/P.NEW_REL/next(iter(data['files']))
        target.write_text('corrupt')
        with self.assertRaises(ValueError): self.stage(runner)
        self.assertEqual(runner.calls,1)
    def test_extra_output_inside_completed_tree_rejected(self):
        folder=self.root/'out';folder.mkdir();(folder/'x').write_text('x')
        receipt=self.root/'receipt.json';P.atomic(receipt,{'status':'completed','binding':'fixed',
            'files':P.inventory(self.root,[folder]),'output_roots':['out']})
        (folder/'unrecorded').write_text('added')
        with self.assertRaises(ValueError):P.verify_receipt(self.root,receipt,'fixed')
    def test_changed_completed_input_fails_closed(self):
        runner=FakePipeline(self.root);self.stage(runner)
        with self.assertRaises(ValueError):self.stage(runner,binding={'source':'different'})
        self.assertEqual(runner.calls,1)
    def test_downstream_failure_does_not_rerun_completed_inference(self):
        runner=FakePipeline(self.root,[0,1]);self.stage(runner)
        with self.assertRaises(RuntimeError):self.stage(runner,'qc')
        self.stage(runner)
        self.assertEqual(runner.calls,2)
        runner.codes=[0];self.stage(runner,'qc',{'source':'fixed','reviewed_helper_version':2})
        self.assertEqual(runner.calls,3)
    def test_transient_retry_exactly_once_preserves_both_attempts(self):
        runner=FakePipeline(self.root,[75,0]);_,receipt=self.stage(runner)
        self.assertEqual(runner.calls,2)
        self.assertEqual(P.read(receipt)['execution_timing']['known_execution_wall_seconds'],2.5)
        self.assertEqual(len(list(receipt.parent.glob('attempt-*'))),2)
    def test_second_transient_failure_stops_and_does_not_retry_on_resume(self):
        runner=FakePipeline(self.root,[75,75])
        with self.assertRaises(RuntimeError):self.stage(runner)
        with self.assertRaises(RuntimeError):self.stage(runner)
        self.assertEqual(runner.calls,2)
    def test_live_child_prevents_adoption_or_duplicate(self):
        runner=FakePipeline(self.root);directory=runner.new/'results/camera/stages/full/attempt-000001';directory.mkdir(parents=True)
        P.atomic(directory/'binding.json',{'binding':P.canonical({'source':'fixed'})})
        P.atomic(directory/'execution.json',{'status':'running','owner':{'host':'unknown-host','pid':123,'proc_start_token':'1'}})
        with self.assertRaises(RuntimeError):self.stage(runner)
        self.assertEqual(runner.calls,0)
    def test_dead_parent_completed_artifacts_adopt_without_execution(self):
        runner=FakePipeline(self.root);directory=runner.new/'results/camera/stages/full/attempt-000001';directory.mkdir(parents=True)
        P.atomic(directory/'binding.json',{'binding':P.canonical({'source':'fixed'})})
        (directory/'output.txt').write_text('verified exact result')
        _,receipt=self.stage(runner)
        self.assertEqual(runner.calls,0);self.assertTrue(P.read(receipt)['adopted_after_parent_restart'])
    def test_archive_idempotence_input_tamper_and_safe_adoption(self):
        source=self.root/'data';source.mkdir();(source/'frame.json').write_text('exact')
        archive=self.root/'data.tar.gz'; first=P.verified_archive(archive,[source]);second=P.verified_archive(archive,[source])
        self.assertEqual(first,second)
        archive.with_name(archive.name+'.receipt.json').rename(self.root/'receipt-preserved.json')
        self.assertEqual(first,P.verified_archive(archive,[source]))
        (source/'frame.json').write_text('changed')
        with self.assertRaises(ValueError):P.verified_archive(archive,[source])
    def test_unreceipted_archive_symlink_and_wrong_content_rejected(self):
        source=self.root/'data';source.mkdir();(source/'a').write_text('exact')
        archive=self.root/'bad.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            member=tarfile.TarInfo('data/a');member.type=tarfile.SYMTYPE;member.linkname='/outside';tar.addfile(member)
        with self.assertRaises(ValueError):P.verified_archive(archive,[source])
        self.assertTrue(archive.exists())
    def test_unknown_owner_is_not_assumed_dead(self):
        self.assertTrue(P.live_owner({'host':P.socket.gethostname(),'pid':123,'proc_start_token':None}))
        with patch.object(P,'proc_token',return_value='current'):
            self.assertFalse(P.live_owner({'host':P.socket.gethostname(),'pid':123,'proc_start_token':'old'}))
    def test_resumed_completion_keeps_original_delivery_identity(self):
        path = self.root/'camera_completion.json'
        original = {'source':'fixed','files':{'digest':'a'},'completed_at':'yesterday','elapsed':12}
        P.immutable_completion(path, original, ('completed_at','elapsed'))
        reused = P.immutable_completion(path, {**original,'completed_at':'today','elapsed':3}, ('completed_at','elapsed'))
        self.assertEqual(reused, original); self.assertEqual(P.read(path), original)
        with self.assertRaises(ValueError):
            P.immutable_completion(path,{**original,'files':{'digest':'changed'}},('completed_at','elapsed'))
    def test_atomic_status_and_fixed_json_never_clobber_other_identity(self):
        path=self.root/'status.json';P.fixed_json(path,{'state':'ready'});P.fixed_json(path,{'state':'ready'})
        with self.assertRaises(ValueError):P.fixed_json(path,{'state':'different'})
        self.assertEqual(P.read(path),{'state':'ready'})
        self.assertEqual(list(self.root.glob('*.writing-*')),[])

if __name__=='__main__':unittest.main()
