"""Build reviewable Chinese report from the audited paired-source outputs."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT.parent
D=json.loads((ROOT/'count_analysis.json').read_text())['results']
M=json.loads((ROOT/'motion/motion_summary.json').read_text())['rows']
A=json.loads((ROOT/'audit/comparability_audit.json').read_text())
EV=json.loads((ROOT/'evidence/evidence_metadata.json').read_text())
ORDER=['Newport','NE12th','Eastgate','SE38th','NE8th']

def link(text,path):return f'[{text}]({Path(path).resolve()})'
def result(case,batch):return next(r for r in D if r['case']==case and r['batch']==batch)
def total(r):return r['statistics'][-1]
def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,row))+' |' for row in rows])
def fmt(x,n=3):return '未知' if x is None else f'{x:.{n}f}'
def num_pair(a,b,key,n=3):return f'{fmt(a[key],n)} → {fmt(b[key],n)}'
def motionrow(c,b,z):return next(r for r in M if r['case']==c and r['batch']==b and r['zone_id']==z)

NARRATIVE={
'Newport':('四个同名区域的模型平均当前数量均下降，但保留各自短时起伏；这一结果支持选区观察对比，不能直接归因于早晚时段。',
'road_right 从3.734降至1.040，road_top 从5.341降至3.124。选区合计的时间加权中位数由12变为5，P90由17变为10；补充源第一刻钟较高，后三刻钟约5.4，并非整段持续下降。',
'road_right 的条件运动中位数5.38→9.82 px/s，但可用时间比例93.3%→48.1%；road_top 则8.44→7.24 px/s。方向并不一致，不能概括成“新源更快”。',
'原源为9月11日早间，补充为9月10日傍晚，日期及照明同时变化。前景设备遮挡、远端小目标和区域尺度限制持续存在。Newport背景二次分辨率确认在两段中均启用，数值规则相同；新源适用性只有稀疏图像审阅支持，过滤准确率没有独立验证。'),
'NE12th':('这是夜间Validation与短白天Train的观察条件对照，只适合说明证据可得性差异，不能用作两段等长独立验证或“白天更拥堵”的结论。',
'原夜间各区域P90均为0，选区合计75.08%的原显示时间为零检测；补充白天的合计中位数8、P90为11。补充仅408.577秒，不能把7.405与原一小时0.303的差值当作日夜交通量效应。',
'原夜间各区运动可用时间仅0%—6.1%，junction_center完全未知；白天Train可用比例提高，但区域间仍为17.1%—78.3%。原夜间仅2至22个可用道路窗口，条件中位数没有稳定对照基础。',
'同一1280×720机位标识、同ROI坐标与数值参数得到核对，但日夜眩光/低照度、来源划分、时长和不同日期共同限制解释。补充Train身份贯穿全部表格、图例和追溯文件；不做准确率、迁移成功或外推验证表述。'),
'Eastgate':('补充源总体观察数量较低，但源内四个约15分钟均值逐步上升；它最适合展示“完整来源的趋势比单一均值更有信息”。',
'原源合计四分段均值23.355→22.057→21.839→19.831；补充源2.894→3.330→5.973→7.778。road_top 均值9.270→1.805、road_upper_left 6.234→1.200，变化需按同名区域分别解读。',
'road_top 的条件运动中位数1.45→10.41 px/s，可用时间98.9%→71.1%；补充road_left没有一个窗口满足运动门槛。数量与运动可以共同描述证据，却不能自动产生真实排队或拥堵判断。',
'原为傍晚较亮画面，补充由黎明转为日光，源首尾原图证实照明变化；因此补充源内数量上升也可能同时受可检出性改善影响。约60秒补充样例选区0、ROI外2，直接说明“选区0”不等于整个画面无车。部分道路没有纳入ROI。'),
'SE38th':('同名区域观察均值均较低，补充源后半段较前半段低；原源长保持帧明显更多，但去除保持区间后不改变均值差异方向。',
'原源最后一刻钟均值19.262，高于前三段；补充四分段为7.503、7.250、5.846、6.297，不能描述成严格单调下降。合计中位数16→7，P90为23→11。',
'road_top 条件运动中位数1.29→6.69 px/s，可用时间89.2%→74.4%；road_left 11.81→30.33，road_right 32.12→31.16，仍存在方向及证据组成差异。',
'原白天与补充黄昏/入夜，补充源首尾照明变暗；前景设备遮挡未消失。原18个长保持帧占98.733秒，补充2个占4.967秒；保持期间不是新的图像证据，更不能视为车辆停止。'),
'NE8th':('两段均为约一小时Validation且配置/ROI相同，但日夜观察数量与运动证据覆盖差距极大；这是最清楚的方法边界案例。',
'合计均值32.210→0.266、中位数32→0，补充夜间75.76%显示时间为选区零检测。road_top 为14.782→0.132。此差异只证明模型输出不同，不足以区分真实需求降低与夜间漏检等因素。',
'补充夜间四区未知时间分别95.3%、90.6%、98.1%、100%；junction_center没有可用运动。少量被检测并持续跟踪的车辆形成条件样本，因此夜间道路区中位数较高不能证明真实道路速度更高或更畅通。',
'日间远端车辆密集且有遮挡，夜间照明不均和车灯眩光明显。两段均无>0.5秒PTS保持，也不代表夜间证据充分。所有正常间隔的像素重复尚未做全视频画面审计。')}

def case_section(case):
 a,b=result(case,'Original'),result(case,'Supplementary');t,u=total(a),total(b);n=NARRATIVE[case]
 parts=[f'## {case}',f'**简短结论：{n[0]}**',
  f'原源：`{a["source_id"]}`，{a["split"]}，{a["checks"]["duration_s"]:.3f}秒。\n\n补充：`{b["source_id"]}`，{b["split"]}，{b["checks"]["duration_s"]:.3f}秒。',
  n[1], '下表均为原 → 补充；均值单位是辆，P10/中位数/P90是按有效原显示时长加权的逐帧当前数量分布，不是10秒均值的分位数。',
  table(['同名区域','加权均值','原 P10 / 中位数 / P90','补充 P10 / 中位数 / P90','零检测时间 %'],[
  [s['zone'],num_pair(s,v,'mean'),f'{s["p10"]} / {s["median"]} / {s["p90"]}',f'{v["p10"]} / {v["median"]} / {v["p90"]}',num_pair(s,v,'zero_pct',1)] for s,v in zip(a['statistics'],b['statistics'])]),
  f'完整曲线、分布：{link("英文数量图",ROOT/f"figures/{case}_counts_and_distribution.png")}；{link("PDF",ROOT/f"figures/{case}_counts_and_distribution.pdf")}。',
  '**运动证据。** '+n[2],
  '以下中位数只来自满足证据条件的10秒窗口；可用时间分母是该来源的全部原PTS时长，余下时间均为未知，含尾窗、保持、无观察和运动不足。',
  table(['区域','运动中位数 px/s 原→补充','可用原时间 % 原→补充','可用窗口数 原→补充'],[[z,num_pair(motionrow(case,'Original',z),motionrow(case,'Supplementary',z),'motion_p50_px_s',2),f'{motionrow(case,"Original",z)["available_motion_time_fraction"]*100:.1f} → {motionrow(case,"Supplementary",z)["available_motion_time_fraction"]*100:.1f}',f'{motionrow(case,"Original",z)["available_motion_windows"]} → {motionrow(case,"Supplementary",z)["available_motion_windows"]}'] for z in a['zones']]),
  link('英文运动证据图',ROOT/f'figures/{case}_motion_evidence.png'),
  f'**缺失与保持。** 两段原记录缺失均为0秒；长保持为{a["checks"]["long_hold_frames"]}段/{a["checks"]["long_hold_s"]:.3f}秒 → {b["checks"]["long_hold_frames"]}段/{b["checks"]["long_hold_s"]:.3f}秒，影响{a["checks"]["hold_affected_10s_windows"]} → {b["checks"]["hold_affected_10s_windows"]}个10秒窗。合计均值在剔除整段长保持帧显示区间后为{t["mean_excluding_long_hold_frames"]:.3f} → {u["mean_excluding_long_hold_frames"]:.3f}。此为时间权重敏感性，不是对真实车辆数的校正。',
  '**可比性与局限。** '+n[3],
  f'图像证据：{link("原/补充并排图",ROOT/f"evidence/{case}_pair.png")}。约60秒位置用来说明场景、ROI和光照，不宣称代表全来源；两个来源也未对齐信号周期。',
  '**可回看的定量示例区间。** 以下按每10秒滑动、连续六个完整无保持窗口自动选出选区合计均值最低与最高的60秒；这是探索性极值，不是随机样本，也不能当总体代表或独立检验。',
  table(['来源','较低观察60秒（PTS秒）／均值','较高观察60秒（PTS秒）／均值'],[[r['batch']+f' ({r["split"]})',f'{r["representative_intervals"]["lowest_complete_60s"]["start_s"]}–{r["representative_intervals"]["lowest_complete_60s"]["end_s"]} / {r["representative_intervals"]["lowest_complete_60s"]["mean"]:.3f}',f'{r["representative_intervals"]["highest_complete_60s"]["start_s"]}–{r["representative_intervals"]["highest_complete_60s"]["end_s"]} / {r["representative_intervals"]["highest_complete_60s"]["mean"]:.3f}'] for r in [a,b]])]
 ep=next(p for p in EV['pairs'] if p['intersection']==case)
 new=next(e for e in ep['entries'] if e['role']=='supplementary');iv=new['preview_source_intervals'][0]
 parts.append(f'已有补充短预览：{link("观看",new["preview_path"])}，覆盖原PTS {iv["source_start_seconds"]:.6f}–{iv["source_end_seconds"]:.6f}秒；播放器0秒对应上述原PTS起点。短预览没有覆盖上述全部高低区间。全部精确帧号/PTS/哈希见视觉证据元数据。')
 if case in ['Eastgate','SE38th']:parts.append(link('补充源首尾光照原图',ROOT/f'evidence/{case}_light_change.png'))
 if case=='Eastgate':parts.append(link('补充较低/较高60秒区间内的原图',ROOT/'evidence/Eastgate_analysis_interval_context.png')+'：实际原PTS 240.032900秒与2770.005189秒。原图保留原像素，无新推理或人工计数；分别只是所选区间中的一个时刻。')
 return '\n\n'.join(parts)

quick='''**十段已完成来源的独立对比已完成。五对的同名ROI几何、模型和数值处理规则一致，可以比较各自选区的模型观察；照明、日期、时长及证据覆盖不同，不构成受控时段实验。**

最有价值的结果是：Eastgate补充源总体数量较低，却在本小时内部上升；NE8th夜间源数量接近零，同时运动证据大部分未知。这两类结果分别展示完整时间分析的用途和方法边界。NE12th补充是408.577秒Train，只作观察补充，不能包装成独立验证。

本文的“原 → 补充”表示两个交付批次，并非时间先后或同一天的连续拍摄。所有“当前数量”都是模型在选定图像区域同时观察到的车辆，既不是真实密度，也不是累计车流；未计算计数或拥堵准确率。'''
sections=['# 五路口原完整案例与补充来源独立对比',quick,
 '## 快速结果',table(['路口（固定列表顺序）','合计平均当前数量 原→补充（辆）','原/补充时长（秒）','可支持的重点'],[[c,num_pair(total(result(c,'Original')),total(result(c,'Supplementary')),'mean'),f'{result(c,"Original")["checks"]["duration_s"]:.3f} / {result(c,"Supplementary")["checks"]["duration_s"]:.3f}',NARRATIVE[c][0]] for c in ORDER]),
 '上表用于按同路口查找结果，不比较不同路口的ROI合计高低或拥堵程度。各机位面积、透视和道路覆盖不同，不能由跨区域占比排名。\n\n'+link('同名区域均值总图',ROOT/'figures/paired_region_means.png'),
 '## 来源、配置与可比性核对',
 '原五段均为Validation；补充四段Validation、一段NE12th Train。10个原视频的SHA已重新计算，来源与冻结划分清单一致；同一路口两源文件不同，文件名时间区间无重叠。文件名时钟仅作标签，时区/设备时钟没有独立校准，分析位置以原PTS为准。',
 table(['路口','原文件名时间标签','补充文件名时间标签','划分','照明对照'],[[c,result(c,'Original')['source_id'].split('__')[1].removesuffix('.mp4'),result(c,'Supplementary')['source_id'].split('__')[1].removesuffix('.mp4'),'Validation / '+result(c,'Supplementary')['split'],{'Newport':'早间日光 / 傍晚','NE12th':'夜间 / 白天短片','Eastgate':'较亮傍晚 / 黎明至日光','SE38th':'白天 / 黄昏至入夜','NE8th':'白天 / 夜间'}[c]] for c in ORDER]),
 '五对均为1280×720，原时间基1/90000秒；同名ROI多边形、区域顺序、bottom_center归属规则逐字段一致。稀疏原图中未见明显机位变化，但本任务没有做全来源画面配准或每帧ROI人工验证；除Newport既有ROI外，其余仍保留provisional几何性质。相同坐标不等于道路物理面积已标定。',
 '模型均为YOLO11s，SHA `07e2280eda613c5d633196729f8716e9715000430171f49f6341afe573a2f334`。推理conf=0.10、imgsz=640、IoU=0.70、max_det=300、half=false；ByteTrack高/新轨阈值0.35、低阈值0.10、buffer=30、match=0.8；跨类别去重IoU=0.70及containment策略均相同。配置device=0和主要运行库版本相同，未将此扩大为物理GPU或位级确定性保证。',
 'Newport两源均采用live_second_resolution背景确认：候选多边形、1280二次分辨率、匹配IoU=0.5、候选确认阈值0.35相同。策略与ROI文件整体SHA因来源绑定及适用性说明变化而不同，不能据此误报为过滤关闭或参数调整。旧/新背景移除条目为20,365/9，单位是逐帧候选条目而非物理车辆；没有GT，不能解释为过滤准确率。其余四对均未启用Newport专属背景过滤。',
 '实现版本差异已审查：Eastgate两源实现相同；其余PTS审计版本把容器nb_frames降为诊断元数据，权威仍是完整解码/EOF与原整数PTS，不插删帧；Newport另有decoded-frame SHA记录范围扩展，对其均启用背景过滤的旧新路径不改变观察计算。不能声称所有文件字节一致，但发现的差异未改变这些输入的有效数值规则。\n\n'+link('字段级可比性审计',ROOT/'audit/comparability_audit.json')+' · '+link('审计检查',ROOT/'audit/audit_checks.json'),
 '## 指标定义和解释边界',
 '1. **PTS加权当前数量**：对每个有效帧的半开呈现区间 `[PTS_i, PTS_i+duration_i)` 积分，均值为Σ(当前数量×区间时长)/Σ有效时长。直接用原整数ticks计算窗口交叠，不使用帧号÷30。所有原10秒均值已从逐帧计数独立复算核对。\n2. **分布与趋势**：P10/中位数/P90使用原显示时长加权的经验CDF逆函数，不插值；趋势展示原10秒曲线和四个等时长分段，NE12th补充每段约102.14秒，其他约15分钟。它们不是独立重复样本，不做显著性检验、置信区间或总体外推。\n3. **保持与缺失**：单帧呈现>0.5秒记作长保持，原数量保留并单列剔除该帧整个显示区间的均值敏感性；运动分析则把任何相交的整个10秒窗口设为未知，清空运动历史。缺失不填0；不足10秒尾窗保留计数但运动未知。\n4. **图像运动**：同ID同区域连续观察的框底部中心，在原PTS约1.0–1.2秒的位移/时间；窗口内先按各ID有效时间求均速，再以各ID有效时间加权中位数。需≥50%车辆观察时间覆盖、≥5车辆·秒且完整无保持窗口。它是px/s，受透视、目标位置、框抖动、遮挡、跟踪断裂和样本选择影响，不是真实道路速度或停车识别。',
 '**本任务不派生补充来源的High/Mid/Low或持续状态，也未做固定基准迁移实验。** 首批P33/P67及运动P25是每来源自身全部合格窗口校准的离线相对基准，不能跨源比较标签、提示比例来论证改善或验证成功。既有20/30/60秒实验只说明首批持续规则敏感性；30秒仍是未用交通状态GT验证的展示规则。运动表中的P25/P50/P75仅为有效运动窗的描述性分位数，没有被用作新相对低运动阈值。\n\n'+link('已完成持续时间敏感性分析',BASE/'persistence_sensitivity_2026-09-28/持续时间门槛敏感性分析.md'),
 '## 每路口分析入口', '\n\n'.join(link(c,ROOT/f'cases/{c}.md') for c in ORDER)]
for c in ORDER:
 text=case_section(c);(ROOT/'cases').mkdir(exist_ok=True);(ROOT/f'cases/{c}.md').write_text('# '+c+'：原与补充来源对比\n\n'+text.split('\n\n',1)[1]+'\n');sections.append(text)
checks=[r['checks'] for r in D]
sections += ['## 全部时间证据与计算核验',table(['路口/批次','原记录缺失（秒）','长保持段数／秒','最长保持（秒）','受影响10秒窗','完整无保持窗/所有窗'],[[r['case']+'/'+r['batch'],fmt(r['missing_s']),f'{r["long_hold_frames"]} / {r["long_hold_s"]:.3f}',fmt(r['long_hold_max_s']),r['hold_affected_10s_windows'],f'{r["eligible_windows"]}/{r["windows"]}'] for r in checks]),
 '共核对978,763帧、32,808.687秒（约9.114小时）、3,289个来源时间窗及14,279个区域窗口。全部原记录缺失为0秒，但这不能排除漏检或正常PTS间隔的重复画面。逐帧时间/时长与独立PTS侧车一致，计数导出SHA与原QC一致，窗口SHA与完成凭据一致；10秒重新积分最大绝对误差小于1×10⁻⁸辆。各区域计数相加与同帧选区合计一致，整段均值与原交付QC一致。',
 '原运动值复用经哈希核验的既有结果；补充运动从完整逐帧跟踪记录重算，并对帧/PTS/配置哈希、运动可用条件、保持窗null和缺失边界核验。比较脚本没有重新运行检测或跟踪，也未把不同ID总数用作车流。所有自动检查证明数据关系与实现一致，不能证明车辆或交通状态预测正确。\n\n'+link('数量核验',ROOT/'count_verification.json')+' · '+link('运动核验',ROOT/'motion/verification.json')+' · '+link('最终交付核验',ROOT/'final_verification.json'),
 '## 明天meeting最值得展示的两个例子',
 '**1. Eastgate：完整时序观察与混杂因素。** 先展示'+link('一小时曲线及四分段均值',ROOT/'figures/meeting_Eastgate_trend.png')+'，说明补充均值2.894→3.330→5.973→7.778；再并列'+link('源首尾原图',ROOT/'evidence/Eastgate_light_change.png')+'，承认黎明到日光会影响可检出性。可以说：“在固定ROI和处理规则下，模型观察随来源内时间变化；光照同步变化，因此尚不能把该趋势单独归因于交通需求。”',
 '**2. NE8th：输出很低不等于证据充分。** 展示'+link('日夜并排图',ROOT/'evidence/NE8th_pair.png')+'与'+link('运动证据覆盖图',ROOT/'figures/NE8th_motion_evidence.png')+'，把32.210→0.266的数量对照与夜间90.6%—100%未知时间一起说明。可以说：“系统可以保留夜间数量观察，但多数区域窗没有足够连续运动证据；我们明确显示未知，不能宣称检测到畅通。”\n\n若导师问独立验证，再补充NE12th的Train/短时长限制；不要将它替代Main Test或宣称交通状态准确率。',
 '## 输出、复现及论文可用范围',
 '本目录为独立版本 `paired_case_comparison_2026-09-28_v1`。原两批、首批状态、持续性分析及Main Test结果保持只读；未读取Main Test图片/标签，未修改Unity，未启停云实例，未追加115段处理、修改模型阈值或新增人工标注。只抽取/复用少量原图及既有短预览，没有重导全长视频。',
 table(['内容','入口'],[['全部数字',link('count_analysis.json',ROOT/'count_analysis.json')],['同名区域数量表',link('region_count_summary.csv',ROOT/'tables/region_count_summary.csv')],['重新积分10秒表',link('window_counts_recomputed.csv',ROOT/'tables/window_counts_recomputed.csv')],['源内四分段趋势',link('quarter_trends.csv',ROOT/'tables/quarter_trends.csv')],['逐区运动与未知比例',link('motion_summary.csv',ROOT/'motion/motion_summary.csv')],['原图/预览精确PTS与哈希',link('evidence_metadata.json',ROOT/'evidence/evidence_metadata.json')],['输入SHA汇总',link('input_hashes.json',ROOT/'input_hashes.json')],['复现说明',link('README.md',ROOT/'README.md')]]),
 '论文可将此作为固定流程在额外Train/Validation来源上的**案例观察、时间证据与方法适用性分析**。没有独立计数/交通状态真值，也没有均衡的时段抽样或重复日期设计，不能写成真实密度/流量估计准确率、拥堵准确率、日夜因果效应或独立外推验证。']
(ROOT/'五路口原案例与补充时段独立对比总报告.md').write_text('\n\n'.join(sections)+'\n')
meeting=[s for s in sections if s.startswith('**1. Eastgate') or s.startswith('**2. NE8th')]
(ROOT/'meeting快速结论.md').write_text('# Meeting快速结论\n\n'+quick+'\n\n'+'\n\n'.join(meeting)+'\n\n'+link('完整总报告',ROOT/'五路口原案例与补充时段独立对比总报告.md')+'\n')
print(ROOT/'五路口原案例与补充时段独立对比总报告.md')
