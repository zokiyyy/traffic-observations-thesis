"""Packaging helper: generate local detector configs without changing research records.
Does not train, evaluate, download data, or modify frozen hashes. Standard library only.
"""
from pathlib import Path
import argparse,copy,json,re,shlex,hashlib
ROOT=Path(__file__).resolve().parents[1]
SYSTEM=ROOT/'formal_research/traffic_yolo_system'

def path(value):
 if not isinstance(value,str) or not value.strip():raise ValueError('Each input path must be a non-empty string')
 p=Path(value).expanduser()
 return (p if p.is_absolute() else ROOT/p).resolve()

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--paths',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 c=json.loads(a.paths.read_text());names=['trainval_root','main_test_root','yolo11n_initial_weights','yolo11s_initial_weights','main_test_model','output_root']
 p={k:path(c[k]) for k in names}
 out=a.output.resolve()
 for target in [out,p['output_root']]:
  for archive in [ROOT/'formal_research',ROOT/'notes']:
   if target==archive or archive in target.parents:raise SystemExit('Generated configs/new results must be outside archived project paths.')
 if out.exists():raise SystemExit('Output folder already exists; choose a new folder.')
 if c['main_test_device'] not in ['mps',0,'0','cpu']:raise SystemExit('Choose a supported explicit evaluation device (mps, 0 or cpu).')
 out.mkdir(parents=True)
 names_yaml='names:\n  0: car\n  1: motorcycle\n  2: bus\n  3: truck\n'
 q=lambda x:json.dumps(str(x))
 (out/'data_trainval.yaml').write_text('path: '+q(p['trainval_root'])+'\ntrain: images/train\nval: images/val\n'+names_yaml)
 (out/'data_main_test.yaml').write_text('path: '+q(p['main_test_root'])+'\ntrain: '+q(p['trainval_root']/'images/train')+'\nval: '+q(p['trainval_root']/'images/val')+'\ntest: images/test\n'+names_yaml)
 for size,template in [('n','training_config.yaml'),('s','nectar/training_yolo11s.yaml')]:
  text=(SYSTEM/'configs'/template).read_text()
  replacements={'experiment_name':'yolo11'+size+'_release_rerun','model':q(p['yolo11'+size+'_initial_weights']),'data':q(out/'data_trainval.yaml'),'project':q(p['output_root']/'training')}
  for key,value in replacements.items():
   text,n=re.subn(r'^'+key+r':.*$',lambda m:key+': '+value,text,flags=re.M)
   assert n==1,key
  (out/f'training_yolo11{size}.yaml').write_text('# Generated path adaptation; original training parameters/devices retained.\n'+text)
 # Relocate image/label locations only; preserve original expected content hashes and source identity.
 source=SYSTEM/'outputs/evaluation/yolo11s_main_test_v1_2026-09-28/preparation/dataset_manifest.json'
 m=copy.deepcopy(json.loads(source.read_text())); mapped=[]
 for e in m['entries']:
  for kind,folder in [('image','images'),('label','labels')]:
   key=kind+'_path';new=p['main_test_root']/folder/'test'/Path(e[key]).name
   for field in [key,'original_'+key]:
    if field in e:mapped.append({'field':field,'from':e[field],'to':str(new)});e[field]=str(new)
 (out/'main_test_manifest.json').write_text(json.dumps(m,indent=2)+'\n')
 models=json.loads((ROOT/'MODEL_MANIFEST.json').read_text())['models'];expected=next(x['copied_sha256'] for x in models if x['role']=='selected detector')
 evaluation={'model':str(p['main_test_model']),'expected_model_sha256':expected,'data':str(out/'data_main_test.yaml'),'dataset_manifest':str(out/'main_test_manifest.json'),'output_dir':str(p['output_root']/'main_test_release_rerun'),'device':c['main_test_device']}
 (out/'main_test_evaluation.json').write_text(json.dumps(evaluation,indent=2)+'\n')
 status={k:{'path':str(v),'exists':v.exists()} for k,v in p.items() if k!='output_root'}
 image_labels=[Path(e[k]) for e in m['entries'] for k in ['image_path','label_path']]
 checked_hashes=False
 if all(v.is_file() for v in image_labels):
  for e in m['entries']:
   for kind in ['image','label']:
    assert hashlib.sha256(Path(e[kind+'_path']).read_bytes()).hexdigest()==e[kind+'_sha256'],('dataset hash mismatch',e[kind+'_path'])
  checked_hashes=True
 if p['main_test_model'].is_file():assert hashlib.sha256(p['main_test_model'].read_bytes()).hexdigest()==expected,'Unexpected Main Test model'
 report={'generated_only':True,'training_or_inference_started':False,'inputs':status,'test_image_label_files_present':sum(v.is_file() for v in image_labels),'test_image_label_files_required':len(image_labels),'exact_test_image_label_hashes_verified':checked_hashes,'path_changes':mapped,'note':'Existing files are not sufficient to establish hardware/environment compatibility. All expected image/label/model hashes remain enforced.'}
 (out/'configuration_report.json').write_text(json.dumps(report,indent=2)+'\n')
 python='python3';commands=[f'{python} {shlex.quote(str(SYSTEM/"src/train.py"))} --config {shlex.quote(str(out/"training_yolo11n.yaml"))} --check-only',f'{python} {shlex.quote(str(SYSTEM/"src/train.py"))} --config {shlex.quote(str(out/"training_yolo11s.yaml"))} --check-only',f'{python} {shlex.quote(str(SYSTEM/"src/evaluate_main_test.py"))} --config {shlex.quote(str(out/"main_test_evaluation.json"))}']
 (out/'NEXT_STEPS.txt').write_text('Generated configurations only. Supply exact data/models and recorded environment first.\nTraining preflight commands do not load a model. The final evaluation command DOES run new inference; use only when intended.\n\n'+'\n'.join(commands)+'\n')
 print(json.dumps({'configuration_created':str(out),'image_label_files_present':report['test_image_label_files_present'],'image_label_files_required':len(image_labels),'training_or_inference_started':False}))
if __name__=='__main__':main()
