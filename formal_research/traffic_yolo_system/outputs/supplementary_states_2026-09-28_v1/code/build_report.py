"""Chinese source-bound research report from audited supplementary state outputs."""
from pathlib import Path
import json
from collections import Counter
OUT=Path(__file__).resolve().parents[1];BASE=OUT.parent
PAIRED=BASE/'paired_case_comparison_2026-09-28_v1'
CASES=['Newport','NE12th','Eastgate','SE38th','NE8th']
def rd(p):return json.loads(p.read_text())
def lk(label,path):return f'[{label}]({path.resolve()})'
def tab(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,row))+' |' for row in rows])
def f(x,n=4):return 'null／不足' if x is None else f'{x:.{n}f}'
idx=rd(OUT/'results_index.json')['results'];R=rd(OUT/'region_summary.json');EVENTS=rd(OUT/'hint_events.json')

TEXT={
'Newport':('可作为完整来源内相对状态的主要补充案例。四区均具备可区分数量阈值及运动P25，但部分窗口仍应显示未知。',
'例如road_left在原PTS [390,400)、[400,410)、[410,420)三个窗口连续满足相对高数量和相对低运动，首次联合提示确认在420秒；提示不能提前画在390秒或410秒。',
'junction_center的运动P25约79.28 px/s，说明“相对低运动”并不等于静止。该源的纯数量持续提示有5个区域窗口同时缺少完整综合证据；这些窗口保留数量提示，主状态仍为unknown。',
'早晚与阴影差异、背景过滤适用性仅稀疏审阅的限制沿用配对审计。可优先加入Unity的来源选择列表，但必须显示本源阈值、原PTS和未知。'),
'NE12th':('单列为408.577秒白天Train补充观察。五区数量阈值可区分，但只有road_right满足运动校准样本数要求；不宜当作完整的一小时Validation演示。',
'完整无保持窗口仅36个，五区运动校准窗口分别为16、24、32、19、7。仅road_right可计算P25≈0.5247 px/s；其他四区即便当前有运动数值，也缺少相对低运动基准，因此综合显示全程unknown。',
'9个高数量提示区域窗口中，7个只有数量证据；没有联合提示。其含义是冻结条件未同时持续成立，不是“没有拥堵”。3个长保持帧共14.200秒，影响4个10秒窗；末窗400–408.576856秒不分类。',
'Unity建议作为可选研究案例，固定标记Train、6分48.577秒与短来源校准；用于展示运动观察和校准资格的区别，不能宣传为独立泛化验证。'),
'Eastgate':('适合与已有原图一起展示黎明至日光来源中的相对观察和证据门槛。五区数量阈值可区分，四区能校准P25。',
'road_left没有任何运动可用窗口，P25为null，综合状态100%未知；其18个高数量持续区域窗口只支持数量提示。P33=0且零检测单列，所以相对低等级可能没有非零窗口，三个等级并非均匀三等分。',
'其余区域共有18个联合提示区域窗口。最早联合例子为road_upper_left，条件区间900–930秒，首次在930秒确认。junction_center的P25≈118.87 px/s，不可解释为停车阈值。',
'源内亮度持续变化已由原图证实，校准基准也混合了这些可见性条件。Unity建议优先加入作为方法展示，road_left必须保持灰色未知，不能用数量颜色替代综合状态。'),
'SE38th':('四个区域均能建立本源数量和运动基准，适合展示持续提示、保持帧中断及未知恢复。',
'联合提示共13个区域窗口。最早例子是road_right，条件区间330–360秒，360秒首次确认。相对运动阈值随透视而不同：road_top约0.71、road_right约14.76、junction_center约115.47 px/s，不可跨区域作为统一速度界限。',
'长保持位于约3100.138–3104.171秒及3316.571–3317.504秒，分别影响3100–3110、3310–3320窗口；该窗不参与校准，两种计时均重置。原数量只作为保持画面观察保留，不当作新运动证据。',
'黄昏至夜间逐步变暗和前景设备遮挡仍存在。Unity建议优先加入，尤其展示保持窗灰色、窗口结束确认及计时重置。'),
'NE8th':('应作为夜间证据不足案例。四区P33=P67=0，无法建立可区分的数量等级，综合状态全程unknown。',
'零观察窗仍保留relative_count_level=empty；正数窗的数量等级为unknown，绝不强制划成low/mid/high。road_top有34个运动可用窗，能计算P25≈11.4739 px/s；其余17、7、0个不足30。不能笼统写成“四区都完全没有运动数值或P25”。',
'数量阈值退化已足以阻断所有综合状态，故0个高数量及0个联合提示。零提示和零检测均不能证明道路空闲；少量运动数值也无法消除夜间眩光、漏检和样本选择限制。',
'可加入Unity的限制展示页：全程灰色unknown，保留原观察数量和具体原因。不适合用绿色“畅通”或低中高拥堵颜色展示。')}

def per_case(c):
 d=rd(OUT/c/'states_10s.json');src=d['source'];rows=[r for r in R if r['case']==c];i=next(x for x in idx if x['case']==c);text=TEXT[c];examples=rd(OUT/c/'examples.json')['examples']
 sec=[f'## {c}',f'**{text[0]}**',f'来源：`{src["id"]}`；**{src["split"]}**；原时长{src["duration_s"]:.6f}秒；{src["frame_count"]:,}帧。',
  f'共有{i["windows"]}个来源时间窗，{i["complete_unheld_windows"]}个完整且无长保持；{i["regions"]}个ROI。原记录缺失为0秒，但不代表每个窗口都有足够运动证据。',
  tab(['区域','数量P33 / P67（辆）','数量校准窗','运动P25（px/s）','运动校准窗','高数量 / 其中联合区域窗','综合未知时间%'],[[r['zone_id'],f'{f(r["count_p33"])} / {f(r["count_p67"])}',r['count_calibration_windows'],f(r['motion_p25_px_s']),r['motion_calibration_windows'],f'{r["high_count_hint_windows"]} / {r["joint_hint_windows"]}',f'{r["evidence_unknown_fraction"]*100:.2f}'] for r in rows]),
  text[1],text[2],text[3],
  lk('英文数量/运动与阈值图',OUT/f'figures/{c}_counts_motion.png')+' · '+lk('英文提示和未知时间轴',OUT/f'figures/{c}_state_timeline.png'),
  '**可审阅的具体窗口。** 自动选取每类最早满足条件的区域窗口，用于解释规则，不代表全来源。这里列出的时间均为原来源相对PTS；所有提示只能在窗结束后确认。',
  tab(['示例类型','区域','窗口 [start,end) 秒','冻结提示 / 综合显示','高数量持续 / 联合持续（秒）'],[[k,e['zone_id'],f'[{e["start_s"]:.3f}, {e["end_s"]:.6f})',e['state_hint']+' / '+e['evidence_state'],f'{e["high_count_duration_s"]:.1f} / {e["joint_duration_s"]:.1f}'] for k,e in examples.items() if e]),
  lk('逐窗JSON',OUT/c/'states_10s.json')+' · '+lk('逐窗CSV',OUT/c/'states_10s.csv')+' · '+lk('阈值与校准窗口索引',OUT/c/'rules_and_thresholds.json')+' · '+lk('提示事件',OUT/c/'hint_events.json')+' · '+lk('原/补充视觉语境',PAIRED/f'evidence/{c}_pair.png')]
 (OUT/c/'中文结果说明.md').write_text('# '+c+'补充来源状态说明\n\n'+'\n\n'.join(sec[1:])+'\n')
 return '\n\n'.join(sec)

totals={k:sum(x[k] for x in idx) for k in ['frames','duration_s','windows','complete_unheld_windows','all_region_windows','unknown_evidence_region_windows','high_count_hint_region_windows','joint_hint_region_windows']}
unknown_count=sum(r['high_count_hints_with_unknown_evidence'] for r in R)
parts=['# 补充五路口交通状态与证据不足研究报告',
 '**5/5补充来源已完成可解释的10秒派生结果，沿用冻结的来源内P33/P67、运动P25和30秒持续规则。** Newport、Eastgate、SE38th可以展示来源内相对变化及持续提示；NE12th必须作为短Train案例单列；NE8th夜间四区数量阈值退化，综合状态全部保留未知。',
 '本轮复用了已经核验的完整来源计数和运动结果，只读取小型10秒证据、阈值方法与完成凭据，没有重新运行YOLO、跟踪器或全量逐帧计算。源内重新校准用于描述本来源，**不是固定基准迁移或外推准确率验证**。',
 '## 先看结果',
 tab(['来源','划分／原时长','完整无保持窗/所有窗','数量可分区/运动可校准区','高数量提示／其中联合（区域窗口）','综合未知（区域窗口）'],[[x['case'],f'{x["split"]} / {x["duration_s"]:.3f}s',f'{x["complete_unheld_windows"]}/{x["windows"]}',f'{x["count_calibrated_regions"]}/{x["motion_calibrated_regions"]}（共{x["regions"]}区）',f'{x["high_count_hint_region_windows"]}/{x["joint_hint_region_windows"]}',f'{x["unknown_evidence_region_windows"]}/{x["all_region_windows"]}'] for x in idx]),
 f'共{totals["frames"]:,}个已交付观察帧、{totals["duration_s"]:.3f}秒、{totals["windows"]:,}个来源窗、{totals["all_region_windows"]:,}个区域窗口；{totals["complete_unheld_windows"]:,}个来源窗完整且无保持。高数量持续提示{totals["high_count_hint_region_windows"]}个区域窗口，其中联合提示{totals["joint_hint_region_windows"]}个；两者不能相加。{unknown_count}个高数量区域窗口缺少综合证据，仍保留纯数量提示而综合显示unknown。',
 '“区域窗口”是ROI×10秒窗，不是独立车辆或视频互不重叠的时长；不同区域同时出现提示会重复计入区域窗口。提示事件为212段高数量事件、35段联合事件；两种事件的分段边界不同，也不相加。综合未知3,798个区域窗口不代表视频缺失时长：来源和ROI时间会重叠，且未知也可能来自校准或运动证据不足。',
 '## 方法与未知的准确含义',
 '数量是选定图像ROI内模型观察到的同时在场车辆，使用原PTS显示时间加权。沿用1/90000秒时间基、10秒半开窗口，不用帧号除以30。计数、ROI、原窗口边界和运动数值均与配对分析缓存逐项核对；原窗口文件的SHA与补充完成凭据一致。',
 '每个来源、每个ROI单独使用完整且无长保持的10秒窗计算P33/P67，分位数为(n−1)q位置的线性插值，包含零观察。非零且≤P33为相对低、≤P67为中、其余高；零单列。P33=P67时非零数量不能强行分级。分位数相同、零值和边界相等意味着等级比例不一定各占三分之一。',
 '运动定义不变：同ID同区域车辆框底部中心约1.0–1.2秒的原PTS位移/时间；先求每ID的时间加权均值，再以有效运动车辆时间取加权中位数。每窗需≥50%观察车辆时间覆盖及≥5车辆·秒。P25只在本区至少30个可用运动窗时计算；相对低运动≤P25，并不意味着停车或低道路速度。',
 '数量相对高连续30秒产生数量提示；数量相对高且相对低运动同时连续30秒产生联合提示。数量条件中断重置数量计时；运动缺失或运动条件中断只重置联合计时；缺失/长保持/不完整窗口重置两者。单帧原呈现>0.5秒定义为长保持，任何重叠的整个10秒窗不参与校准与持续计时；上一步已清空保持前后的运动历史。',
 '**两个层次分别保存，避免把纯数量证据误显示为完整交通状态。** 冻结`state_hint`完全保留首批规则，可能在运动不足时仍给出high_count_persistent。新增`evidence_state`是保守综合显示：窗口不完整/有保持/有缺失、数量阈值退化、当前运动不足或P25不足，任一条件存在便为unknown。只有这些条件均通过，才显示joint_hint、high_count_hint或no_persistent_hint；最后一种也不代表畅通。',
 tab(['情形','保留数值/提示','综合显示'],[['有数量，但当前运动覆盖不足','数量及可判定的数量等级；motion_median_px_s=null；单独数量计时可继续','unknown'],['有绝对运动数值，但运动校准窗<30','保留绝对px/s；motion_low_relative=null；P25=null','unknown'],['零观察','count=0、relative_count_level=empty；不推断真实道路无人','unknown'],['长保持或不足10秒末窗','保留原数量以追溯；相对等级unknown、运动null、两种计时重置','unknown'],['P33=P67','零观察单列；非零数量等级unknown；即使运动可用也不补造数量等级','unknown'],['完整证据但尚无30秒持续提示','可解释的数量等级和相对运动；无持续提示','no_persistent_hint，不等于free flow']]),
 '全部综合状态与纯数量提示分开存储：未来UI应以evidence_state为综合显示，以high_count_persistent_count_only作单独数量提示；不能让冻结state_hint覆盖灰色unknown。纯数量标志包括联合提示的数量条件，因此联合是其子集。未知原因数组允许多项同时成立，原因时长不能直接相加。',
 '**确认时间为窗口结束的原PTS。** 例如连续三个10秒条件窗口[390,400)、[400,410)、[410,420)，最早在420秒确认，不提前到第三窗起点410秒。这里的阈值使用整个来源离线校准，420秒是回放解释时间，不是实时系统当时已经有全部校准数据的声明。',
 '## 来源内校准与固定阈值迁移不能混写',
 '本轮只完成每个补充来源自身的离线校准。补充来源的High/Mid/Low或提示比例不能与原来源重新校准后的标签直接比较，也不能把“新来源有提示”当作迁移成功。绝对数量/运动比较仍应查阅已完成的配对报告及其照明限制。',
 '本轮没有运行固定原基准迁移实验。若后续另做，必须冻结原源各ROI阈值及30秒规则，用补充证据代入，另列原阈值来源/SHA、几何/数值配置可比性、未知规则和光照限制。已完成配对审计显示五对ROI与数值规则一致，但Newport背景适用性仅稀疏检查，日夜可检出性、非等长NE12th Train均限制解释。原夜间NE12th本身数量阈值退化，不能构造有效三档“迁移验证”。没有独立交通状态GT，任何迁移结果也只能是规则稳定性或适用性实验。',
 lk('原/补充配对总报告',PAIRED/'五路口原案例与补充时段独立对比总报告.md')+' · '+lk('可比性审计',PAIRED/'audit/两批来源可比性审计.md'),
 '## 分来源说明']
for c in CASES:parts.append(per_case(c))
parts+=['## 哪些案例值得加入Unity（本轮仅建议）',
 tab(['建议顺序','来源','展示价值与限制'],[['优先','SE38th','四区具备数量及运动基准；可展示30秒联合提示及保持帧导致计时重置。黄昏光照限制常驻。'],['优先','Newport','完整来源内相对提示流程；同时展示有数量持续提示但运动不足的灰色unknown。'],['优先','Eastgate','黎明源内变化与证据门槛；road_left必须全程unknown，不能误用纯数量颜色。'],['限制展示','NE8th','夜间数量校准退化，四区全程unknown；用来说明少检测和无提示不等于畅通。'],['可选研究页','NE12th','明确Train、408.577秒和仅一区运动可校准；不作为一小时独立Validation。']]),
 '接入时保留“来源—划分—原时长—版本”标签；显示相对当前数量和图像运动，避免“真实密度/速度/拥堵”。使用实际ROI顶点和原PTS，不把补充30秒短预览的播放时钟当全源时间。短预览只能展示已声明来源区间，不能配着它播放全小时状态。若未来只用静态/图形回放，必须标明“原时间轴离线回放”。',
 '建议将灰色unknown与数量持续提示并列，给出当前运动不足、P25校准不足、保持帧或数量阈值退化的原因；联合提示只在窗口结束确认。已存在的Unity数据、逻辑和媒体本轮均未修改。',
 '## 校验、复现和成果边界',
 '本任务新目录为`supplementary_states_2026-09-28_v1`。冻结engine字节SHA为`1ca1c8f7eb024e99b78e96cb72c2adbd545ba61635c950f7d70fdf0da5062390`，与首批最终校验一致；只调用其calibrate/classify_windows。综合unknown显示层单独版本化，没有改模型、阈值数值算法或原成果。',
 '复用前校验补充交付完成、首批方法完成、配对分析完成及运动独立核验；缓存motion_10s与两套完成凭据中的SHA一致。新窗口与原windows.jsonl的来源、划分、计数和时间逐项相同。原逐帧原始数据SHA沿用已核验的证据链，本阶段没有重新遍历全量帧日志或原视频。',
 '独立核验检查每区线性分位数、29/30个运动校准窗口边界、20/30秒确认、缺失/保持/尾窗/时间不连续重置、运动不足仅中断联合条件、冻结提示与综合未知显示关系。核验数量不应被写成模型准确率。',
 lk('派生完成凭据',OUT/'derivation_completion.json')+' · '+lk('独立核验与边界检查',OUT/'verification/README.md')+' · '+lk('最终交付核验',OUT/'final_verification.json')+' · '+lk('输入SHA清单',OUT/'input_manifest.json')+' · '+lk('复现说明',OUT/'README.md'),
 '未读取Main Test图片/标签、未重跑检测跟踪、未导出全长带框视频、未修改Unity或其他窗口队列、未新增人工标注、未启停云实例。报告可作为模型观察、证据门槛与方法局限的研究分析；没有真实密度、车流、道路速度或拥堵准确率结论。']
(OUT/'补充五路口交通状态与证据不足研究报告.md').write_text('\n\n'.join(parts)+'\n')
print(OUT/'补充五路口交通状态与证据不足研究报告.md')
