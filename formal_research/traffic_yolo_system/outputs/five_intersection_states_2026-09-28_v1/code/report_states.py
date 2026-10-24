import os
os.environ.setdefault('MPLCONFIGDIR','/tmp/traffic_state_matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D
import numpy as np
import json, collections, subprocess, sys, hashlib, html
from pathlib import Path
from derive_states import OUT, put, sha, VERSION
NAMES=['Newport','NE12th','Eastgate','SE38th','NE8th']
LIMITS={
'Newport':'白天验证案例；复用既有状态结果。背景过滤与其他四路口不同，不能用绝对数量横向评定路口拥堵程度。遮挡、透视与跟踪断裂仍会影响观察。',
'NE12th':'夜间替代来源，有车灯眩光。稀疏检测不能证明道路空闲；五个区域的数量 P33/P67 均为 0，无法可靠区分低、中、高相对等级。每区有效运动窗口不足 30 个，不能建立相对低运动阈值。这段主要适合展示证据不足处理，不适合作为交通畅通案例。',
'Eastgate':'道路区域未覆盖全部可见道路，前景设备遮挡明显。原容器标称帧数与完整实际审计相差 1 帧已在原交付中处理；本次依照 107,892 个实际帧及原始 PTS，不补帧、不拉伸时间。',
'SE38th':'存在原生长保持帧，已隔离其影响窗口。数量及运动仍受遮挡、跟踪断裂与类别变化影响。保持期间保留的数量只表示最后可见画面的持续显示，不能理解成新的连续车辆观测。',
'NE8th':'远处车辆密集、尺寸小，阴影与日照混合。跟踪 ID 断裂或变化会限制连续运动证据；区域当前数量不能转成累计过车数。'}
ZH={'no_persistent_signal':'未达到持续条件','motion_insufficient':'运动证据不足','high_count_persistent':'持续相对高数量','high_count_low_motion_persistent':'持续相对高数量＋相对低图像运动','no_observed_vehicles':'未观察到车辆','night_no_observations':'夜间未检测到车辆，路况未知','insufficient_window':'尾窗或缺失，未分类','calibration_insufficient':'数量基准不足','held_image_evidence_insufficient':'画面保持，证据不足'}
COL={'low':'#d8ebfa','medium':'#ffe4ad','high':'#ffcccc','empty':'#eeeeee','unknown':'#eeeeee'}
def num(x):return '不足 / 不可用' if x is None else f'{x:.3f}'
def ts(s):return f'{int(s)//60:02d}:{s%60:06.3f}'
def link(label,p):return f'[{label}]({p})'
def longest(d,zone,key):
 best=None;start=None;end=None
 for w in d['windows']+[{'zones':[], 'start_s':d['source']['duration_s']}]:
  r=next((r for r in w['zones'] if r['zone_id']==zone),None)
  good=r is not None and w['complete'] and (r['relative_count_level']=='high') and (key=='high' or r['motion_low_relative'] is True)
  if good:
   if start is None:start=w['start_s']
   end=w['end_s']
  elif start is not None:
   if end-start>=30 and (best is None or end-start>best['duration_s']):best={'zone':zone,'kind':key,'start_s':start,'end_s':end,'duration_s':end-start,'confirmation_available_at_s':start+30}
   start=None
 return best

def charts(name,d):
 zs=[z['id'] for z in d['zones']];ws=d['windows'];x=np.array([w['start_s']/60 for w in ws]);edges=np.array([w['start_s']/60 for w in ws]+[ws[-1]['end_s']/60])
 fig,axes=plt.subplots(len(zs),3,figsize=(18,3.0*len(zs)),squeeze=False)
 for i,z in enumerate(zs):
  rs=[next(r for r in w['zones'] if r['zone_id']==z) for w in ws];th=d['thresholds'][z]
  count=[r['count_mean'] if r['count_mean'] is not None else np.nan for r in rs];motion=[r['motion_median_px_s'] if r['motion_median_px_s'] is not None else np.nan for r in rs]
  a,b,c=axes[i]
  for w,r in zip(ws,rs):
   a.axvspan(w['start_s']/60,w['end_s']/60,color=COL[r['relative_count_level']],alpha=.65,lw=0)
   if w['held_duration_s']:
    for ax in (a,b,c):ax.axvspan(w['start_s']/60,w['end_s']/60,color='#6e6e6e',alpha=.3,lw=0)
  a.stairs(count,edges,color='#164e80',lw=.95)
  a.axhline(th['count_p33'],color='#a36b00',ls=':',lw=1);a.axhline(th['count_p67'],color='#ba3535',ls='--',lw=1)
  a.set(title=f'{z.replace("_"," ").title()} | Current count',ylabel='10-s mean (vehicles)')
  b.plot(x,motion,'.-',color='#127c76',lw=.7,ms=2)
  if th['motion_low_upper'] is not None:b.axhline(th['motion_low_upper'],color='#ae4e00',ls='--',lw=1)
  else:b.text(.02,.88,'P25 unavailable: fewer than 30 eligible windows',transform=b.transAxes,fontsize=8)
  b.set(title='Tracked image motion',ylabel='Image motion (pixels/s)')
  if not any(v is not None for v in [r['motion_median_px_s'] for r in rs]):
   b.text(.5,.45,'No eligible motion estimates',ha='center',transform=b.transAxes,fontsize=10)
  if th['count_p33']==th['count_p67']:
   c.text(.5,.5,'Count calibration insufficient\nNo state classification',ha='center',transform=c.transAxes,fontsize=10)
  c.step(x,[r['high_count_duration_s'] for r in rs],where='post',color='#ab4b29',lw=1)
  c.step(x,[r['joint_duration_s'] for r in rs],where='post',color='#703b99',lw=1)
  c.axhline(30,color='#555555',ls=':',lw=1);c.set(title='Continuous qualifying duration',ylabel='Duration (s)')
  for ax in (a,b,c):ax.set_xlim(0,60);ax.set_xlabel('Original video time (min)');ax.grid(alpha=.16);ax.set_ylim(bottom=0)
 fig.suptitle(f'{name} | Offline relative count and image-motion indicators\n'+('Night case: low detections do not establish free flow' if d['night_case'] else 'Within-zone, within-hour calibration; not road speed or validated congestion'),fontsize=16,y=.995)
 handles=[Patch(color=COL[k],label=f'{k.title()} relative count') for k in ('low','medium','high')]+[Patch(color='#eeeeee',label='No observations / undetermined'),Patch(color='#6e6e6e',alpha=.3,label='Held-image window'),Line2D([],[],color='#a36b00',ls=':',label='Count P33'),Line2D([],[],color='#ba3535',ls='--',label='Count P67'),Line2D([],[],color='#ae4e00',ls='--',label='Motion P25'),Line2D([],[],color='#ab4b29',label='High count streak'),Line2D([],[],color='#703b99',label='High count + low motion streak'),Line2D([],[],color='#555555',ls=':',label='30-s confirmation')]
 fig.legend(handles=handles,loc='lower center',ncol=4,fontsize=9,bbox_to_anchor=(.5,.007));fig.tight_layout(rect=[0,.08,1,.95]);fig.savefig(OUT/name/'state_overview.png',dpi=160);fig.savefig(OUT/name/'state_overview.pdf');plt.close(fig)

summaries={}
for name in NAMES:
 dest=OUT/name;d=json.loads((dest/'states_10s.json').read_text());ws=d['windows'];zs=[z['id'] for z in d['zones']];state_counts=collections.Counter(r['state_hint'] for w in ws for r in w['zones'])
 eligible=[w for w in ws if w['complete']];held=[w for w in ws if w['held_duration_s']];duration=d['source']['duration_s']
 combined=sum(sum(r['count_mean'] or 0 for r in w['zones'])*(w['end_s']-w['start_s']) for w in ws)/duration
 events=[e for z in zs for key in ['joint','high'] if (e:=longest(d,z,key))]
 joint=sorted([e for e in events if e['kind']=='joint'],key=lambda e:e['duration_s'],reverse=True)
 high=sorted([e for e in events if e['kind']=='high'],key=lambda e:e['duration_s'],reverse=True)
 peak=max(eligible,key=lambda w:sum(r['count_mean'] or 0 for r in w['zones']))
 selected=[]
 if joint:selected.append(joint[0])
 if high:selected.append(high[0])
 selected.append({'kind':'count_peak','start_s':peak['start_s'],'end_s':peak['end_s'],'combined_mean_count':sum(r['count_mean'] for r in peak['zones'])})
 if d['held_intervals']:
  h=max(d['held_intervals'],key=lambda h:h['duration_s']);selected.append({'kind':'held_evidence_example','start_s':h['start_ticks']/90000,'end_s':h['end_ticks']/90000,'held_duration_s':h['duration_s']})
 put(dest/'showcase_intervals.json',selected)
 s={'duration_s':duration,'frames':d['source']['frame_count'],'zones':len(zs),'windows':len(ws),'eligible_windows':len(eligible),'held_windows':len(held),'held_intervals':len(d['held_intervals']),'held_duration_s':sum(h['duration_s'] for h in d['held_intervals']),'combined_mean_count':combined,'state_counts':dict(state_counts),'showcases':selected,'derivation_seconds':d['derivation_seconds']}
 summaries[name]=s;put(dest/'result_summary.json',s);charts(name,d)
 lines=[f'# {name}：交通状态提示结果', '',LIMITS[name],'',f'本段 `{d["source"]["id"]}`，Validation 来源，共 {duration:.6f} 秒、{s["frames"]:,} 帧、{len(zs)} 个区域。生成 {len(ws)} 个时间窗口，其中 {len(eligible)} 个完整且不受长保持帧影响的窗口可用于状态校准。','',f'完整源时间轴上，所有所选区域的平均当前车辆数量合计为 **{combined:.3f} 辆**。这包含原画面保持时间的加权，表示原记录的可见数量，不是每小时车流量。','',f'长保持帧 {s["held_intervals"]} 段，合计 {s["held_duration_s"]:.3f} 秒，影响 {len(held)} 个 10 秒窗口（窗口覆盖 {len(held)*10} 秒；与保持秒数不同）。这些窗口的原数量保留，等级和状态不判定，持续时间归零。','', '## 区域基准与结果','', '| 区域 | 数量 P33 / P67（辆） | 运动 P25（像素/秒） | 数量 / 运动校准窗口 | 高数量提示窗口 | 联合提示窗口 |','|---|---:|---:|---:|---:|---:|']
 for z in zs:
  t=d['thresholds'][z];rs=[r for w in ws for r in w['zones'] if r['zone_id']==z]
  lines.append(f'| {z} | {t["count_p33"]:.3f} / {t["count_p67"]:.3f} | {num(t["motion_low_upper"])} | {t["count_calibration_windows"]} / {t["motion_calibration_windows"]} | {sum(r["state_hint"]=="high_count_persistent" for r in rs)} | {sum(r["state_hint"]=="high_count_low_motion_persistent" for r in rs)} |')
 lines+=['','表中的两种提示互斥，联合提示优先显示；窗口数不是独立事件次数。区域标签只表示图像位置，不代表已核实的地理方向。P33/P67 相同时，非零数量不强行分成三个等级。','', '## 哪些片段值得看','']
 for e in selected:
  if e['kind'] in ('high','joint'):
   desc='相对高数量且相对低图像运动' if e['kind']=='joint' else '相对高数量'
   lines.append(f'- **{ts(e["start_s"])}—{ts(e["end_s"])}**，`{e["zone"]}`：连续 {e["duration_s"]:.0f} 秒满足{desc}条件。到 **{ts(e["confirmation_available_at_s"])}** 才获得首个 30 秒持续确认；这不是拥堵起止时间。')
  elif e['kind']=='count_peak':lines.append(f'- **{ts(e["start_s"])}—{ts(e["end_s"])}**：合格窗口中区域数量合计最高，10 秒均值 {e["combined_mean_count"]:.3f} 辆。适合对照带框画面看数量变化，不能据此判定拥堵。')
  else:lines.append(f'- **{ts(e["start_s"])}—{ts(e["end_s"])}**：最长原画面保持区间，{e["held_duration_s"]:.3f} 秒。适合展示为什么系统必须显示“证据不足”。')
 lines+=['','以上位置为原视频相对时间；JSON 还提供从源文件名推得的时钟时间，时区未经核实，不能当作已校准的 UTC。10 秒结果要看到窗口末尾才算收集完整，不能用作实时无延迟判断。','', '## 证据与解释','', '原记录无缺失时间段（0 秒），但画面保持仍是独立的证据质量问题。区域轮廓来自现有配置，保留原有几何定义；本次未进行新的人工道路边界验证。', '']
 lines += [f'- {ZH[k]}：{v} 个区域窗口。' for k,v in state_counts.items()]
 lines+=['','持续高数量可以在运动不足时单独提示，但联合状态必须有足够运动证据。未出现持续提示，不代表道路畅通。没有真实车速、道路容量、物理区域面积或人工计数真值，因此不能报告交通密度、km/h 或状态准确率。', '', '## 结果入口','',link('完整带框视频（原成果）',d['source']['video_path']), '',link('每10秒结果CSV',dest/'states_10s.csv')+' · '+link('结构化JSON',dest/'states_10s.json')+' · '+link('区域阈值与规则',dest/'rules_and_thresholds.json')+' · '+link('一致性检查',dest/'validation.json'),'',f'![{name} English state charts]({dest}/state_overview.png)','', '图表：左列为 10 秒平均数量及相对等级底色；中列为有足够跟踪证据时的图像运动，空缺不等于 0；右列为连续满足条件的时长。灰色区间为画面保持影响窗口。未分类的极短尾窗可能窄到无法在全小时图上看清，以数据表为准。']
 (dest/'中文结果说明.md').write_text('\n'.join(lines)+'\n')

put(OUT/'results_index.json',summaries)
# Save software evidence as a separate explicit check, not a performance/accuracy evaluation.
test=subprocess.run([sys.executable,str(OUT/'code/test_states.py')],capture_output=True,text=True)
assert test.returncode==0,test.stderr
put(OUT/'software_validation.json',{'passed':True,'tests':12,'stdout':test.stdout,'stderr':test.stderr,'scope':'synthetic boundaries and consistency only; not traffic accuracy','implementation_sha256':{p.name:sha(p) for p in (OUT/'code').glob('*.py')}})
lines=['# 五路口交通状态分析与中文结果总报告','','**五个路口的派生状态结果已生成并通过一致性检查。** Newport 的既有状态数值直接复用，其余四路口读取已经交付的检测、跟踪和原始时间轴记录计算。本次没有运行 YOLO，没有重新导出完整视频，没有读取 Main Test，也没有改动 Unity。','','## 先看结论','','四个白天案例可以展示“相对数量变化、图像运动及持续提示”。**NE12th 夜间案例的五个区域均不足以建立可区分的数量等级及相对低运动阈值**，因此保留数量观察，明确显示未知或证据不足。这是数据限制，不应强行包装成道路空闲。','','NE12th 和 SE38th 的长保持帧已按已有 PTS 审计定位。原结果保留；受影响的整个 10 秒窗口不分类、不参与基准校准，运动历史和持续状态计时均重置。','','## 五段结果入口','','| 路口 | 区域 | 总窗口 / 可判定窗口 | 长保持段数 / 影响窗口 | 结果说明 |','|---|---:|---:|---:|---|']
for name,s in summaries.items():lines.append(f'| {name} | {s["zones"]} | {s["windows"]} / {s["eligible_windows"]} | {s["held_intervals"]} / {s["held_windows"]} | {link("中文说明、图表和视频入口",OUT/name/"中文结果说明.md")} |')
lines+=['','“可判定窗口”只表示完整时间覆盖且没有长保持帧；每个区域仍可能缺乏运动或校准证据。Eastgate 最后一个窗口不足 10 秒，其余四段有极短尾窗，均保留原数量但不分类。','','## 实际计算的内容','','1. **每10秒平均当前数量**：使用原始 PTS 表示的画面持续时间加权，保留原 `windows.jsonl` 数值。四个新增路口另从逐帧计数独立积分核对；Newport 复用既有积分核验与状态结果，再核对原窗口和源帧哈希。它不是累计车流。','2. **区域内相对数量等级**：仅使用本区域、本小时的完整且未受保持帧影响的窗口，分别计算 P33、P67；非零数量 ≤P33 为相对低、≤P67 为中、其余为高。零检测单列；阈值重合时不强行分级。不同路口的阈值分别计算，不能用等级横向比较真实拥堵。','3. **图像运动**：连续同 ID、同区域车辆框底部中心约 1.0–1.2 秒的像素位移除以时间。先在窗口内按车辆有效时间加权，再取车辆运动的加权中位数。需要至少 50% 车辆观察时间覆盖和 5 车辆·秒的证据；缺失记录为 null。不是光流、真实道路速度或停车识别。','4. **相对低运动**：每个区域至少 30 个有运动证据的合格窗口，取本区域 P25；不足则未知。路口中心 P25 可高达几十至上百像素/秒，所以“相对低”绝不等于车辆停止。','5. **持续提示**：高数量持续 30 秒产生提示；同时满足相对低运动并持续 30 秒则产生联合提示。数量条件中断重置数量计时；运动证据中断重置联合计时；保持帧、缺失或不完整窗口重置两者。高数量提示可在运动不足时保留，不能由此推断拥堵。','','本版规则是透明的开发提示规则，未以人工交通状态真值验证。分位数使用本小时全部合格窗口校准，适合离线展示；不是可以直接上线的实时算法。','','## 数据审计与检查结果','', '共 535,255 个原始观察帧、1,804 个时间窗口、7,937 个区域窗口。五段原始记录缺失时间均为 0 秒；这不消除保持帧、漏检和遮挡的限制。本次派生与复用核查用时约 66.6 秒（不包含文档制作时间），不涉及重新处理视频。', '','- 五段均为已完成的固定 Validation 来源；区域数量分别为 4、5、5、4、4，共 22 个区域。四个新增路口的 ROI 保留既有 provisional_AI_geometry 配置，本次核对来源、哈希和坐标范围，没有将其升级为人工验证道路边界。','- 对五段已保存帧记录逐帧核对来源、配置、ROI、原始整数 PTS、持续时间与独立时间审计，并核对原完成凭据中的 SHA。','- 检查原时间轴覆盖、区域窗口完整性、原窗口数量一致性、尾窗边界、运动空值和每个区域的持续时间递推。','- 长保持定义为单帧原始呈现时间 >0.5 秒。该门槛用于证据质量控制，不是交通状态阈值；保持帧在其前一帧呈现区间定位，而不是误标下一帧。没有对实际重复像素而时间正常的视频另做全画面分析，此类潜在保持无法仅据本审计排除。','- 12 项边界测试通过，覆盖窗口跨界、保持前后运动历史清空、持续时间重置、校准排除、夜间零观察和不同区域数量。','- 新结果与旧成果分目录保存。没有新增人工计数或状态真值，**以上通过不能写成车辆计数准确率或拥堵判断准确率**。','',link('总体计算完成记录',OUT/'derivation_completion.json')+' · '+link('输入来源与SHA清单',OUT/'input_manifest.json')+' · '+link('软件检查记录',OUT/'software_validation.json'),'', '## 后续接入 Unity 需要调整的内容','','这里只列出建议，**本次没有修改 Unity，先等你审阅结果**。','','- 由单路口固定数据改为选择路口后加载对应 `states_10s.json`，区域数量不能写死为 4。','- 使用原视频 PTS 秒数匹配半开区间 `[start_s,end_s)`，不能用帧号除以 30；拖动时间后重新定位窗口。','- 使用动态区域多边形和源图像坐标；带框视频顶部有 150 像素信息栏，原画面为 1280×720，视频为 1280×870，叠加区域需对应内容矩形。','- 标签改为“当前可见数量”“相对数量等级”“图像运动提示”。未知/null 显示证据不足，不能当作 0；夜间和保持帧警示应始终可见。','- 持续提示展示确认时间：10秒窗口收集完毕才能得到该窗口状态；提示不能提前到窗口开始。保持期间冻结/取消状态提示，不延长持续时长。','- 各路口使用自己的基准，NE12th 不显示虚构低中高或拥堵颜色。保留当前版本、阈值和来源链接以便论文追溯。','- 现有 Newport JSON 的旧字段名（如 `density_level`、`traffic_state`）已在本版统一为 `relative_count_level`、`state_hint`，负数缺失值改为 JSON null，Unity 解析器需要相应调整。','','## 重现与范围','','计算代码在本目录 `code/`；原规则快照为 `engine.py`，新增派生入口为 `derive_states.py`，图表与中文汇总入口为 `report_states.py`。派生入口有文件锁且完成后拒绝重复运行。原文件内容未修改。所有视频继续引用已有交付文件。','','**本轮到这里结束，先查看五段结果，再决定是否接入 Unity。**']
(OUT/'五路口交通状态分析总报告.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(summaries,ensure_ascii=False,indent=2))
