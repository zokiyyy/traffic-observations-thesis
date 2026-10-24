"""Figures from completed derived results only. English plot text; no state levels."""
from pathlib import Path
import json, os
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'code/.mpl_cache'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
C={'Original':'#2166AC','Supplementary':'#D6604D'}
data=json.loads((ROOT/'count_analysis.json').read_text())['results']
motion=json.loads((ROOT/'motion/motion_summary.json').read_text())['rows']
for case in ['Newport','NE12th','Eastgate','SE38th','NE8th']:
 zones=next(r['zones'] for r in data if r['case']==case)
 fig,axes=plt.subplots(1,2,figsize=(13,5.3))
 for off,batch in zip([-.15,.15],C):
  rows=[next(r for r in motion if r['case']==case and r['batch']==batch and r['zone_id']==z) for z in zones]
  y=np.arange(len(zones))+off
  label=batch+(' (Train, 6.81 min)' if rows[0]['split']=='Train' else ' (Validation)')
  bars=axes[0].barh(y,[r['available_motion_time_fraction']*100 for r in rows],height=.27,color=C[batch],label=label)
  axes[0].bar_label(bars,fmt='%.1f%%',padding=2,fontsize=8)
  for j,r in enumerate(rows):
   if r['motion_p50_px_s'] is None:
    axes[1].text(0,y[j],'unknown (0 windows)',color=C[batch],va='center',fontsize=8)
   else:
    p25,p50,p75=[r[f'motion_p{k}_px_s'] for k in [25,50,75]]
    axes[1].errorbar(p50,y[j],xerr=[[p50-p25],[p75-p50]],fmt='o',capsize=3,color=C[batch],label=label if j==0 else None)
    axes[1].annotate(f'n={r["available_motion_windows"]}',(p75,y[j]),xytext=(5,0),textcoords='offset points',va='center',fontsize=8,color=C[batch])
 for a in axes:
  a.set_yticks(np.arange(len(zones)),[z.replace('_',' ') for z in zones]);a.set_ylim(len(zones)-.35,-.6);a.grid(axis='x',alpha=.18)
 axes[0].set(xlim=(0,113),xlabel='Source duration with usable motion evidence (%)',title='Evidence coverage (unknown is the complement)')
 axes[0].legend(loc='lower right',fontsize=8)
 axes[1].set(xlabel='Image motion (source-image px/s)',title='Available-window median and IQR (conditional)');axes[1].margins(x=.25)
 fig.suptitle(f'{case} | Motion evidence and conditional distribution',fontsize=14)
 fig.text(.02,.02,'1.0–1.2 s same-ID displacement; >=50% exposure coverage and >=5 vehicle-s per 10 s. IQR is not uncertainty. Unknown is not zero speed.',fontsize=8)
 fig.tight_layout(rect=[0,.05,1,.94]);fig.savefig(ROOT/f'figures/{case}_motion_evidence.png',dpi=170);fig.savefig(ROOT/f'figures/{case}_motion_evidence.pdf');plt.close(fig)

# Meeting example 1: duration-matched quarter trends and full PTS curve.
fig,axes=plt.subplots(2,1,figsize=(12,7.5),gridspec_kw={'height_ratios':[1.5,1]})
for batch in C:
 r=next(r for r in data if r['case']=='Eastgate' and r['batch']==batch)
 w=[w for w in r['windows'] if w['zone']=='TOTAL_SELECTED_ROIS' and w['duration_s']>9.99]
 axes[0].plot([(x['start_s']+x['end_s'])/120 for x in w],[x['mean'] for x in w],color=C[batch],lw=1,label=batch+' (Validation)')
 q=[q for q in r['quarters'] if q['zone']=='TOTAL_SELECTED_ROIS'];values=[q['mean'] for q in q]
 axes[1].plot([7.5,22.5,37.5,52.5],values,marker='o',lw=2,color=C[batch],label=batch)
 for x,v in zip([7.5,22.5,37.5,52.5],values):axes[1].annotate(f'{v:.2f}',(x,v),xytext=(0,7),textcoords='offset points',ha='center',color=C[batch])
for a in axes:
 a.set(xlabel='Original relative PTS (min)',ylabel='Mean current count (vehicles)',xlim=(0,60));a.grid(alpha=.18);a.legend(fontsize=9)
axes[0].set_title('10 s time-weighted count across selected ROIs')
axes[1].set_title('Four equal-duration quarters (approximately 15 min each)');axes[1].margins(y=.25)
fig.suptitle('Eastgate | Lower observations overall, rising within the supplementary source',fontsize=14)
fig.text(.02,.018,'Original: 2017-09-11 17:08:33 | Supplementary: 2017-09-11 06:08:30 (filename clocks; timezone unverified). No causal time-of-day claim.',fontsize=8)
fig.tight_layout(rect=[0,.045,1,.95]);fig.savefig(ROOT/'figures/meeting_Eastgate_trend.png',dpi=180);fig.savefig(ROOT/'figures/meeting_Eastgate_trend.pdf');plt.close(fig)
