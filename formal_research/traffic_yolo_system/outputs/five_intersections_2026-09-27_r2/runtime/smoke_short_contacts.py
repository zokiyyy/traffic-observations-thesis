#!/usr/bin/env python3
"""Exercise short contact rendering using only generated codec-smoke artifacts."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    p=argparse.ArgumentParser();p.add_argument('--generated-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--src-root',type=Path,required=True)
    args=p.parse_args();new=Path(__file__).resolve().parent.parent
    sys.path.insert(0,str(new/'validation'));sys.path.insert(0,str(new/'presentation'))
    import selection_binding
    source=args.generated_dir/'synthetic_variable_pts.mp4'
    smoke=json.loads((args.generated_dir/'codec_smoke_report.json').read_text())
    if smoke.get('synthetic') is not True or smoke.get('model_or_dataset_used') is not False:
        raise ValueError('Only generated codec fixture is supported')
    args.output.mkdir(parents=True,exist_ok=False)
    attempt=args.output/'attempt';attempt.mkdir()
    rows=[json.loads(x) for x in (args.generated_dir/'frames.jsonl').read_text().splitlines()][2:5]
    (attempt/'frames.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    provenance=json.loads((args.generated_dir/'provenance.json').read_text())
    provenance['config']={'segment':{'start_seconds':rows[0]['source_timestamp_seconds'],'end_seconds':1067025/90000}}
    provenance['camera']['layout_path']=str(args.generated_dir/'zones.json')
    provenance['preflight']['frame_count']=3
    (attempt/'provenance.json').write_text(json.dumps(provenance))
    selection=args.output/'synthetic_selection.json';selection.write_text('{"synthetic":true}\n')
    spec=importlib.util.spec_from_file_location('short_contacts',Path(__file__).with_name('short_contacts.py'))
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    argv=['short_contacts.py','--selection',str(selection),'--source-id',source.name,'--source',str(source),'--attempt',str(attempt),
          '--output',str(args.output/'contacts'),'--src-root',str(args.src_root)]
    # Only scope metadata is mocked; original decode, source hash, pixel SHA,
    # record geometry, PTS checks and rendered PNGs execute unmodified.
    entry={'source_sha256':selection_binding.sha(source)}
    with patch.object(sys,'argv',argv),patch.object(selection_binding,'selected_entry',return_value=entry),\
         patch.object(selection_binding,'bind_prepared',return_value=tuple(provenance[k] for k in ('task','config','camera','preflight'))):
        assert helper.main()==0
    report=json.loads((args.output/'contacts/contact_receipt.json').read_text())
    assert report['pixels_verified'] and [row['frame_index'] for row in report['frames']]==[2,3,4]
    result={'state':'passed','synthetic_only':True,'three_exact_original_pixel_frames':[2,3,4],
            'scope_guards_mocked_for_generated_fixture_only':True,'model_or_dataset_used':False,
            'contact_helper_sha256':selection_binding.sha(Path(__file__).with_name('short_contacts.py'))}
    (args.output/'contact_smoke_report.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':main()
