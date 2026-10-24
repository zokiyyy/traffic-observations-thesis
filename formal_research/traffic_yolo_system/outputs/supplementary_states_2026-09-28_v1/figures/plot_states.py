#!/usr/bin/env python3
"""Plot frozen supplementary-source state JSON without deriving or changing states.

Each marker for an evidence state is placed at the source window END.  Unknown
motion stays NaN.  Count-only persistence is displayed in a separate lane and is
never substituted for the main evidence state.  All writes stay under figures/.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent
os.environ.setdefault('MPLCONFIGDIR', str(OUT/'.mpl_cache'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator
import numpy as np

CASES = ['Newport','NE12th','Eastgate','SE38th','NE8th']
STATE_INFO = {
    'unknown': (0, '#858C96', 'Unknown evidence'),
    'no_persistent_hint': (1, '#377EB8', 'No persistent hint'),
    'high_count_hint': (2, '#E18922', 'High-count hint'),
    'joint_hint': (3, '#8B4BA6', 'High count + low motion'),
}
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,
                     'axes.spines.top':False,'axes.spines.right':False,
                     'axes.titleweight':'bold','savefig.facecolor':'white'})

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def fnum(value):
    return np.nan if value is None else float(value)

def svalue(value):
    return 'unavailable' if value is None else f'{value:.3g}'

def save(fig, name):
    paths=[]
    for suffix in ['png','pdf']:
        p=OUT/f'{name}.{suffix}'
        fig.savefig(p,dpi=190)
        paths.append({'path':str(p),'sha256':sha(p),'bytes':p.stat().st_size})
    plt.close(fig)
    return paths

def quality_bad(w):
    return not w['full_valid_window'] or not w['complete']

def annotate_quality(ax, windows):
    for w in windows:
        if quality_bad(w):
            ax.axvspan(w['start_s'],w['end_s'],facecolor='#E0E2E6',alpha=.7,zorder=0)

def source_title(case, source):
    return f"{case} | {source['split']} | {source['duration_s']:.3f} s | supplementary source"

def header(fig,case,source,subtitle,handles):
    height=fig.get_size_inches()[1]
    fig.text(.5,1-.16/height,source_title(case,source),ha='center',va='top',fontsize=15)
    fig.text(.5,1-.43/height,subtitle,ha='center',va='top',fontsize=14)
    fig.text(.5,1-.73/height,source['id'],ha='center',va='top',fontsize=10)
    fig.legend(handles=handles,ncol=3,loc='upper center',bbox_to_anchor=(.5,1-.96/height),frameon=False,fontsize=9)
    return 1-1.63/height

def validate(data,rules):
    source=data['source']
    assert source['id']==rules['source']['id']
    assert source['sha256']==rules['source']['sha256']
    windows=data['windows']
    assert windows and abs(windows[0]['start_s'])<1e-8
    assert abs(windows[-1]['end_s']-source['duration_s'])<1e-5
    zone_ids=[z['id'] for z in data['zones']]
    for i,w in enumerate(windows):
        assert w['end_s']>w['start_s']
        assert abs(w['confirmation_source_time_s']-w['end_s'])<1e-8
        if i: assert abs(windows[i-1]['end_s']-w['start_s'])<1e-6
        assert [z['zone_id'] for z in w['zones']]==zone_ids
        for z in w['zones']:
            assert z['evidence_state'] in STATE_INFO, z['evidence_state']
            assert isinstance(z['high_count_persistent_count_only'],bool)
            if z['evidence_state']!='unknown':
                assert z['motion_low_relative'] is not None
                assert w['complete'] and w['full_valid_window']
    return zone_ids

def plot_signals(case,data,rules,zone_ids):
    source=data['source']; windows=data['windows']
    duration=source['duration_s']
    ends=np.array([w['end_s'] for w in windows])
    edges=np.array([w['start_s'] for w in windows]+[windows[-1]['end_s']])
    n=len(zone_ids)
    fig,axes=plt.subplots(n,2,figsize=(15.8,2.6*n),sharex=True,squeeze=False)
    for i,zone in enumerate(zone_ids):
        rows=[w['zones'][i] for w in windows]
        th=rules['thresholds'][zone]
        a,b=axes[i]
        counts=np.array([fnum(r['count_mean']) for r in rows])
        motion=np.array([fnum(r['motion_median_px_s']) for r in rows])
        a.stairs(counts,edges,color='#2368A5',lw=1.05,zorder=2)
        for key,color,style in [('count_p33','#4F955B','--'),('count_p67','#CD6732',':')]:
            if th[key] is not None:
                a.axhline(th[key],color=color,ls=style,lw=1.25,zorder=3)
        count_title=f"{zone} | P33={svalue(th['count_p33'])}, P67={svalue(th['count_p67'])}"
        if not th['count_thresholds_distinguishable']:
            count_title+=' | P33=P67: degenerate'
        a.set_title(count_title,loc='left',fontsize=10.5)
        a.set_ylabel('Mean current count\n(vehicles)',fontsize=9.5)
        a.set_ylim(bottom=0)
        a.yaxis.set_major_locator(MaxNLocator(nbins=4))
        # Gaps remain missing; a zero speed is plotted only if explicitly measured.
        b.plot(ends,motion,color='#3A8079',marker='.',markersize=2.1,lw=.75)
        p25=th['motion_low_upper']
        ncal=th['motion_calibration_windows']
        if p25 is not None:
            b.axhline(p25,color='#9C4393',ls='--',lw=1.3)
        b.set_title(f"{zone} | P25={svalue(p25)} | calibration windows={ncal}",loc='left',fontsize=11)
        b.set_yscale('symlog',linthresh=1)
        b.set_ylabel('Image motion median\n(px/s; symlog scale)',fontsize=9.5)
        b.set_ylim(bottom=0)
        if not np.any(np.isfinite(motion)):
            b.text(.5,.5,'No usable motion windows\nP25 unavailable: 0 < 30 calibration windows\nUnknown is not zero motion',transform=b.transAxes,
                   ha='center',va='center',color='#666D77',fontsize=11)
            b.set_ylim(0,1)
        elif p25 is None:
            b.text(.98,.96,f'P25 unavailable: {ncal} < 30 usable calibration windows',transform=b.transAxes,
                   ha='right',va='top',color='#6C4E72',fontsize=8.5)
        for ax in [a,b]:
            annotate_quality(ax,windows)
            ax.set_xlim(0,duration)
            ax.grid(alpha=.15)
            ax.xaxis.set_major_locator(MaxNLocator(nbins=7))
    for ax in axes[-1]: ax.set_xlabel('Original source PTS (s)')
    handles=[Line2D([0],[0],color='#2368A5',label='10 s time-weighted count'),
             Line2D([0],[0],color='#4F955B',ls='--',label='Count P33'),
             Line2D([0],[0],color='#CD6732',ls=':',label='Count P67'),
             Line2D([0],[0],color='#3A8079',label='Usable motion median'),
             Line2D([0],[0],color='#9C4393',ls='--',label='Motion P25'),
             Patch(color='#E0E2E6',label='Invalid/held/partial window')]
    top=header(fig,case,source,'Counts and image-motion evidence',handles)
    height=fig.get_size_inches()[1]
    fig.text(.025,.32/height,'Thresholds are calibrated offline within this source and ROI. They are not road capacity or held-out validation. Motion is not road speed.',fontsize=9)
    fig.text(.025,.13/height,'Counts may be retained in invalid windows; their main evidence state is unknown. Missing motion remains a gap. State confirmation occurs at window end.',fontsize=9)
    fig.tight_layout(rect=[.01,.65/height,1,top],h_pad=1.5)
    return save(fig,f'{case}_counts_motion')

def plot_states(case,data,rules,zone_ids):
    source=data['source'];windows=data['windows'];duration=source['duration_s']
    ends=np.array([w['end_s'] for w in windows])
    n=len(zone_ids)
    fig=plt.figure(figsize=(15.8,2.75*n+1))
    gs=fig.add_gridspec(n*2,1,height_ratios=[3,1]*n,hspace=.20)
    last_ax=None
    for i,zone in enumerate(zone_ids):
        rows=[w['zones'][i] for w in windows]
        a=fig.add_subplot(gs[2*i],sharex=last_ax)
        b=fig.add_subplot(gs[2*i+1],sharex=a)
        for state,(y,color,label) in STATE_INFO.items():
            mask=np.array([r['evidence_state']==state for r in rows])
            a.scatter(ends[mask],np.full(mask.sum(),y),marker='s',s=11,
                      color=color,edgecolors='none',zorder=3)
        a.set_yticks([0,1,2,3],['Unknown','No persistent hint','High-count hint','Joint hint'])
        a.set_ylim(-.45,3.45)
        unknown_windows=sum(r['evidence_state']=='unknown' for r in rows)
        a.set_title(f'{zone} | unknown {unknown_windows}/{len(rows)} windows',loc='left',fontsize=11,pad=3)
        a.grid(axis='x',alpha=.16)
        a.grid(axis='y',alpha=.1)
        a.tick_params(axis='x',labelbottom=False)
        count_only=np.array([r['high_count_persistent_count_only'] for r in rows])
        b.scatter(ends[count_only],np.ones(count_only.sum()),color='#B77B0C',marker='D',s=12)
        b.set_yticks([1],['Count-only >=30 s'])
        b.set_ylim(.5,1.5)
        b.grid(axis='x',alpha=.16)
        b.spines['left'].set_visible(False)
        if not count_only.any():
            b.text(.012,.5,'No count-only persistence confirmations',transform=b.transAxes,va='center',fontsize=8.5,color='#747B85')
        if i < n-1: b.tick_params(axis='x',labelbottom=False)
        b.set_xlim(0,duration)
        b.xaxis.set_major_locator(MaxNLocator(nbins=8))
        last_ax=b
    last_ax.set_xlabel('Original source PTS at window END / confirmation time (s)')
    handles=[Line2D([0],[0],marker='s',ls='',color=color,label=label,markersize=5)
             for y,color,label in STATE_INFO.values()]
    handles.append(Line2D([0],[0],marker='D',ls='',color='#B77B0C',label='Separate count-only persistence',markersize=5))
    top=header(fig,case,source,'Evidence states and 30 s persistence confirmations',handles)
    height=fig.get_size_inches()[1]
    fig.text(.025,.50/height,'Markers report evidence at the END of each 10 s window; a 30 s hint cannot be confirmed before the third qualifying window ends.',fontsize=9)
    fig.text(.025,.31/height,'Unknown includes held/partial/missing windows or insufficient count/motion calibration. No persistent hint does not mean free flow.',fontsize=9)
    fig.text(.025,.12/height,'Count-only persistence is a separate quantity-only result and does not override unknown main evidence. Offline source-relative rules; no accuracy claim.',fontsize=9)
    fig.subplots_adjust(left=.145,right=.985,bottom=1.10/height,top=top)
    return save(fig,f'{case}_state_timeline')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',choices=CASES,action='append')
    args=parser.parse_args()
    cases=args.case or CASES
    manifest={'schema':'supplementary-state-figure-v1','state_markers_at':'window_end_s',
              'threshold_scope':'same-source offline relative calibration','figures':[],
              'cases':[],'inputs':[],'no_states_recomputed':True,'unknown_motion_replaced_by_zero':False}
    for case in cases:
        data_path=ROOT/case/'states_10s.json';rules_path=ROOT/case/'rules_and_thresholds.json'
        data=json.loads(data_path.read_text());rules=json.loads(rules_path.read_text())
        ids=validate(data,rules)
        manifest['inputs'] += [{'path':str(p),'sha256':sha(p)} for p in [data_path,rules_path]]
        manifest['figures'] += plot_signals(case,data,rules,ids)
        manifest['figures'] += plot_states(case,data,rules,ids)
        manifest['cases'].append({'case':case,'source':data['source'],'zones':ids,
                                  'windows':len(data['windows']),'schema_checks_passed':True})
    manifest['script_sha256']=sha(__file__)
    name='figure_manifest.json' if len(cases)==5 else 'figure_manifest_'+'_'.join(cases)+'.json'
    (OUT/name).write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({'cases':cases,'figures':len(manifest['figures']),'manifest':str(OUT/name)}))

if __name__=='__main__':
    main()
