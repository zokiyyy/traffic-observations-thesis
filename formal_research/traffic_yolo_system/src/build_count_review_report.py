"""Build a transparent development review from frozen AI visual observations.

No training, inference, parameter changes or human ground truth is created here.
"""
from pathlib import Path
import base64
import csv
import hashlib
import html
import json

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / 'outputs/video_pipeline/yolo11s_newport_val_120s_v1_2026-09-23'
B = PILOT / 'count_review_2026-09-24'
ZONE_NAMES = {'road_left':'左侧道路','road_top':'上方道路','road_right':'右侧道路','junction_center':'路口中央'}

def read(path): return json.loads(path.read_text())
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def image_data(path):
    mime = 'image/png' if path.suffix.lower()=='.png' else 'image/jpeg'
    return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode()

def main():
    protocol = read(B/'review_protocol.json')
    frozen = read(B/'blind_reviews_frozen.json')
    for name, expected in frozen['blind_reviews'].items():
        assert digest(B/name) == expected, 'Initial visual reviews must remain unchanged'
    a,b,r = [read(B/name) for name in ['blind_review_a.json','blind_review_b.json','blind_review_root.json']]
    peer = {o['time_seconds']:o['zones'] for o in a['observations']+b['samples']}
    own = {(o['time_seconds'],o['zone_name']):o for o in r['rows']}
    model = read(B/'selected_model_records.json')
    # Preserve the frozen, prediction-blind observations. Subsequent review is
    # explicitly prediction-informed and must not replace independent references.
    adjudications = {
        (45,'road_right'):{'min':2,'max':3,'exact':None,'status':'initial_visual_reference_revised',
            'note':'连续画面发现灯杆后真实公交车和远端小车；初次助手漏看。复查估计2–3辆，小车临近边界，不能判为程序多计。'},
        (65,'road_right'):{'min':4,'max':4,'exact':4,'status':'initial_visual_reference_revised',
            'note':'连续画面确认第4辆是灯杆后真实车辆；初次助手漏看。程序4辆与复查参考相同。'},
        (85,'road_right'):{'min':2,'max':2,'exact':2,'status':'confirmed_background_overcount',
            'note':'程序把墙/围栏背景当成truck（ID648），本帧多计1辆。'},
        (95,'road_right'):{'min':4,'max':4,'exact':4,'status':'confirmed_background_overcount',
            'note':'程序把墙/围栏背景当成truck（ID778），本帧多计1辆。'},
    }
    for name in ['postcomparison_right_review.json','postcomparison_top_review.json']:
        assert (B/name).is_file(), f'Missing adjudication evidence: {name}'
    rows=[]
    for t in protocol['sample_times_seconds']:
        for z in ZONE_NAMES:
            p,q=peer[t][z],own[(t,z)]
            lo,hi=min(p['min'],q['min']),max(p['max'],q['max'])
            exact = p['exact'] if p['exact'] is not None and p['exact']==q['exact'] else None
            n = model[str(t)]['zone_counts'][z]
            status = ('clear_match' if n==exact else 'clear_disagreement') if exact is not None else ('uncertain_below_range' if n<lo else 'uncertain_above_range' if n>hi else 'uncertain_within_range')
            adj = adjudications.get((t,z))
            rows.append({'time_seconds':t,'frame_index':t*30,'zone_name':z,'zone_zh':ZONE_NAMES[z],
                         'assistant_reference_exact':exact,'assistant_reference_min':lo,'assistant_reference_max':hi,
                         'model_count':n,'model_minus_reference':n-exact if exact is not None else None,
                         'status':status,'human_confirmed':False,
                         'postcomparison_reference_min':adj['min'] if adj else None,
                         'postcomparison_reference_max':adj['max'] if adj else None,
                         'postcomparison_reference_exact':adj['exact'] if adj else None,
                         'postcomparison_status':adj['status'] if adj else 'not_adjudicated',
                         'postcomparison_note':adj['note'] if adj else '',
                         'postcomparison_is_prediction_informed':bool(adj),
                         'reference_note':'Two AI source-image reviews; union of uncertainty ranges, not a confidence interval or human ground truth.'})
    exact_rows=[r for r in rows if r['assistant_reference_exact'] is not None]
    summary={'review_type':'Development count audit using AI visual references; not independent human evaluation',
             'source_split':'Validation','sample_times_seconds':protocol['sample_times_seconds'],
             'zone_cells':len(rows),'initial_exact_agreement_reference_cells':len(exact_rows),
             'initial_exact_reference_model_matches':sum(r['status']=='clear_match' for r in rows),
             'initial_exact_reference_model_differences':sum(r['status']=='clear_disagreement' for r in rows),
             'initial_uncertain_reference_cells':sum(r['assistant_reference_exact'] is None for r in rows),
             'initial_uncertain_below_range_cells':sum(r['status']=='uncertain_below_range' for r in rows),
             'initial_visual_reference_revised_cells':2,
             'confirmed_background_overcount_cells':2,
             'initial_difference_interpretation':'Four initial numeric disagreements are NOT four confirmed model errors; two arose from assistant visual misses of occluded real vehicles.',
             'adjudication_reference_kind':'Prediction-informed AI source-video review; not independent human ground truth.',
             'postcomparison_evidence_hashes':{n:digest(B/n) for n in ['postcomparison_right_review.json','postcomparison_top_review.json']},
             'formal_accuracy_reported':False,'human_confirmed_cells':0,
             'release_assessment':'Needs correction and review; current counts are a development prototype, not validated congestion input.',
             'model_roi_parameters_changed':False,'initial_visual_review_hashes':frozen['blind_reviews']}
    (B/'count_comparison.json').write_text(json.dumps({'summary':summary,'rows':rows},ensure_ascii=False,indent=2)+'\n')
    with (B/'逐项计数对照.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    with (B/'人工复核填写表.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['片段秒','区域','人工确认数量_看不清留空','是否看不清','说明','复核人','复核日期'])
        w.writerows([[r['time_seconds'],r['zone_zh'],'','','','',''] for r in rows])
    notes={
        15:'右侧和中央与两份目视参考一致。左边缘和上方远处有不确定目标，不能把落在范围内当作检查通过。',
        45:'右侧初次助手只数到1辆；连续画面发现被灯杆遮挡的公交车和远端小车，复查估计2–3辆，程序3辆。这里不能判为程序多计。',
        65:'右侧初次助手漏看灯杆后的车辆，复查为4辆，与程序相同。上方程序6辆；远处身份和分界不清，不能用目视范围直接推算漏车数。',
        85:'右侧目视2辆，程序3辆；额外truck框覆盖墙/围栏位置。上方密集车辆仅有不确定范围。',
        95:'右侧4辆，程序5辆：墙/围栏被当成truck。上方程序3辆；有两处可辨认车辆已有低置信检测框，却没有进入当前跟踪计数，具体原因仍需跟踪过程诊断。',
        115:'右侧与中央一致；左侧遮挡车和上方临界车辆归属仍需人工确认。'}
    comparisons=[]
    for t in protocol['sample_times_seconds']:
        comparisons.append({'time':t,'note':notes[t],'rows':[r for r in rows if r['time_seconds']==t],
            'roi':image_data(B/'blind'/f't{t:03d}_roi.jpg'),
            'clean':image_data(B/'blind'/f't{t:03d}_clean.jpg'),
            'model':image_data(B/'comparison'/f't{t:03d}_model.jpg'),
            'top':image_data(B/'blind'/f't{t:03d}_top_clean.jpg'),
            'right':image_data(B/'comparison'/f't{t:03d}_right_model.jpg')})
    boundary=read(B/'boundary_review.json')
    boundary_html=''.join(f'<details><summary>{html.escape(c["case_id"])} · {c["outside_interval"]["clip_start_seconds"]:.2f} 秒</summary><p>{html.escape(c["conclusion_zh"])}</p><img src="{image_data(Path(c["sequence_image"]))}" alt="原图与边界归属连续帧对照"></details>' for c in boundary['cases'])
    adjudication_html=''.join(f'<details><summary>{t}秒：为什么纠正初次目视判断？</summary><p>{html.escape(adjudications[(t,"road_right")]["note"])}</p><img src="{image_data(B/"postcomparison_right_evidence"/f"t{t:03d}_signal_sequence.jpg")}" alt="灯杆附近连续原始画面"></details>' for t in [45,65])
    payload=json.dumps(comparisons,ensure_ascii=False).replace('</','<\/')
    template='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>车辆计数核对 · Newport</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f2f4f7;color:#172331;font:16px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}main{max-width:1180px;margin:auto;padding:28px}h1{font-size:30px;line-height:1.3;margin:0 0 8px}h2{font-size:21px;margin-top:0}.muted{color:#586879}.card{background:white;border:1px solid #dce1e8;border-radius:14px;padding:22px;margin:18px 0}.notice{background:#fff1d5;border-left:5px solid #d48a09;padding:14px 18px;border-radius:8px}nav{display:flex;gap:9px;flex-wrap:wrap;margin:18px 0}button{background:white;border:1px solid #bac5d3;color:#203952;border-radius:8px;padding:10px 16px;font:inherit;cursor:pointer}button.active,button.primary{background:#193e65;color:white;border-color:#193e65}button:focus-visible,input:focus-visible{outline:3px solid #3296dc;outline-offset:2px}img{display:block;width:100%;height:auto;border-radius:7px;background:#e9edf3}.tools{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;margin:12px 0}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #e1e6ec;white-space:nowrap}th{color:#536172;font-size:14px}.bad{color:#ad2633;font-weight:650}.uncertain{color:#86620c}.ok{color:#24694b}input[type=number]{width:80px;padding:8px;border:1px solid #bbc6d2;border-radius:5px}input[type=text]{width:100%;min-width:150px;padding:8px;border:1px solid #bbc6d2;border-radius:5px}details{margin:14px 0}summary{cursor:pointer;font-weight:600;padding:10px 0}.small{font-size:14px}footer{padding:24px 0;color:#5e6b7b}@media(max-width:720px){main{padding:15px}.card{padding:15px}.grid{grid-template-columns:1fr}h1{font-size:25px}}@media print{button,nav,.inputs{display:none}body{background:white}.card{break-inside:avoid}}</style>
<main><p class="muted small">Newport · 两分钟验证视频 · 6个新增时间点</p><h1>车辆数量，数对了吗？</h1>
<p>先看原画面，再显示程序结果。彩色线是固定统计区域；车辆框底边中点决定归属。下方被遮挡道路未统计。</p>
<div class="notice"><strong>本轮发现计数问题，当前版本尚未通过可靠性核对。</strong><br>初次目视有4处明确数值差异。连续复查发现：2处是助手漏看遮挡车辆；另2处是程序把墙/围栏当成卡车。远处和区域边界也有待处理的问题。这是开发抽查，不是人工真值或整段准确率。</div>
<nav id="times" aria-label="选择时间点"></nav>
<section class="card"><h2 id="frameTitle"></h2><div class="tools"><button id="roiBtn" class="active">原图＋分区</button><button id="cleanBtn">纯原图</button><button id="modelBtn">显示程序结果</button></div><p class="muted small" id="modeNote"></p><img id="mainImage" alt="所选时间点画面"><details><summary>放大看上方远处原图</summary><img id="topImage" alt="上方原图放大；不会增加原始细节"></details><div id="modelSection" hidden><p id="sampleNote"></p><div class="tablewrap"><table><thead><tr><th>区域</th><th>初次AI目视</th><th>连续画面复查</th><th>程序数量</th><th>结论</th></tr></thead><tbody id="resultRows"></tbody></table></div><details><summary>放大看右侧程序框</summary><img id="rightImage" alt="右侧程序检测框"></details></div></section>
<section class="card inputs"><h2>你的人工复核记录（选填）</h2><p class="muted small">没有替你填写任何人工结论。看不清请勾选，数量留空。切换时间会保留本页输入；关闭前点击导出保存。数据仅在本机，不会上传。</p><div class="tablewrap"><table><thead><tr><th>区域</th><th>你确认的数量</th><th>看不清</th><th>说明</th></tr></thead><tbody id="inputRows"></tbody></table></div><button id="exportBtn" class="primary">导出我填写的记录</button><span id="exportStatus" class="muted small" aria-live="polite"></span></section>
<section class="card"><h2>初次目视也会出错</h2><p>下列复查已看过程序预测，只用于诊断。初始记录未被覆盖；修正后的数量仍不是独立人工真值。</p>__ADJUDICATION__</section>
<section class="card"><h2>三个边界案例</h2><p>下面是按缓存轨迹挑出的定点问题案例，不代表随机抽样的错误率。</p>__BOUNDARY__</section>
<footer>本页沿用已有YOLO11s、跟踪设置和道路分区，没有重训或修改旧视频。目视范围是可辨认程度的记录，不是统计置信区间。数值落在不确定范围内，也不等于已验证正确。</footer></main>
<script id="review-data" type="application/json">__DATA__</script><script>
const samples=JSON.parse(document.getElementById('review-data').textContent);let current=0,mode='roi',viewedPredictions=false;const entries={};
const $=id=>document.getElementById(id);const ref=r=>r.assistant_reference_exact!==null?String(r.assistant_reference_exact):r.assistant_reference_min+'–'+r.assistant_reference_max+'（不确定）';
function save(){document.querySelectorAll('#inputRows tr').forEach(tr=>{const z=tr.dataset.zone;const n=tr.querySelector('input[type=number]').value;entries[samples[current].time+'|'+z]={time_seconds:samples[current].time,zone_name:z,count:n===''?null:Number(n),uncertain:tr.querySelector('input[type=checkbox]').checked,note:tr.querySelector('input[type=text]').value};});}
function render(){const s=samples[current];$('frameTitle').textContent=s.time+' 秒 · 原视频 '+(1600+s.time)+' 秒';$('mainImage').src=s[mode];$('topImage').src=s.top;$('rightImage').src=s.right;$('sampleNote').textContent=s.note;$('modeNote').textContent=mode==='model'?'带框的是程序输出；编号不是累计车辆总数。':'当前未显示预测框和程序数量。先按彩色区域观察，再进行比较。';$('modelSection').hidden=mode!=='model';['roi','clean','model'].forEach(m=>$(m+'Btn').classList.toggle('active',m===mode));document.querySelectorAll('#times button').forEach((b,i)=>b.classList.toggle('active',i===current));
$('resultRows').innerHTML=s.rows.map(r=>{let state=r.status==='clear_match'?'与初次参考相同':r.status==='clear_disagreement'?'初次差异，待核实':r.status==='uncertain_below_range'?'低于初次估计，需诊断':'目视不确定，待确认';let cls=r.assistant_reference_exact===null?'uncertain':'ok';let revised='—';if(r.postcomparison_is_prediction_informed){revised=r.postcomparison_reference_exact!==null?String(r.postcomparison_reference_exact):r.postcomparison_reference_min+'–'+r.postcomparison_reference_max+'（边界不确定）';state=r.postcomparison_note;cls=r.postcomparison_status==='confirmed_background_overcount'?'bad':'uncertain';}return '<tr><td>'+r.zone_zh+'</td><td>'+ref(r)+'</td><td>'+revised+'</td><td>'+r.model_count+'</td><td style="white-space:normal;min-width:200px" class="'+cls+'">'+state+'</td></tr>'}).join('');
$('inputRows').replaceChildren();s.rows.forEach(r=>{const tr=document.createElement('tr');tr.dataset.zone=r.zone_name;tr.innerHTML='<td>'+r.zone_zh+'</td><td><input type="number" min="0" step="1" aria-label="'+r.zone_zh+'人工数量"></td><td><input type="checkbox" aria-label="'+r.zone_zh+'看不清"></td><td><input type="text" aria-label="'+r.zone_zh+'复核说明"></td>';const e=entries[s.time+'|'+r.zone_name];if(e){tr.querySelector('input[type=number]').value=e.count??'';tr.querySelector('input[type=checkbox]').checked=e.uncertain;tr.querySelector('input[type=text]').value=e.note;}$('inputRows').append(tr);});}
samples.forEach((s,i)=>{const b=document.createElement('button');b.textContent=s.time+' 秒';b.onclick=()=>{save();current=i;mode='roi';render()};$('times').append(b)});
['roi','clean','model'].forEach(m=>$(m+'Btn').onclick=()=>{save();mode=m;if(m==='model')viewedPredictions=true;render()});
$('exportBtn').onclick=()=>{save();const rows=Object.values(entries);if(rows.some(r=>r.count!==null&&(!Number.isInteger(r.count)||r.count<0))){$('exportStatus').textContent=' 数量需为非负整数，或留空。';return;}const payload={kind:'User-entered review; not automatically verified',source_split:'Validation',created_at:new Date().toISOString(),review_context:'AI conclusions visible on page; this is not a blinded independent review',predictions_viewed_in_this_page:viewedPredictions,rows};const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='my_count_review.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('exportStatus').textContent=' 已导出；请保留下载文件。';};render();
</script></html>'''
    (B/'计数核对页面.html').write_text(template.replace('__BOUNDARY__',boundary_html).replace('__ADJUDICATION__',adjudication_html).replace('__DATA__',payload),encoding='utf-8')
    # The short report stays separate from interactive human entries.
    table=['| 片段时间 | 左侧：初次目视 / 程序 | 上方：初次目视 / 程序 | 右侧：初次目视 / 程序 | 中央：初次目视 / 程序 |','|---|---|---|---|---|']
    for t in protocol['sample_times_seconds']:
        cells=[]
        for r in [r for r in rows if r['time_seconds']==t]:
            ref=str(r['assistant_reference_exact']) if r['assistant_reference_exact'] is not None else f"{r['assistant_reference_min']}–{r['assistant_reference_max']}（不确定）"
            cells.append(f"{ref} / {r['model_count']}")
        table.append('| '+str(t)+'秒 | '+' | '.join(cells)+' |')
    report=f'''# 新一轮车辆计数核对

本次沿用原来的模型、跟踪参数和四个分区，只检查结果，没有重新训练、运行YOLO推理或修改旧成果。来源是Validation两分钟片段；未使用Main Test。

**结论：当前版本仍可用于开发演示，但尚未通过计数可靠性核对，暂不直接用它标注拥堵状态。**

[打开交互核对页面]({B/'计数核对页面.html'})：先看原图，点击“显示程序结果”再对照；可填写并导出你的人工记录。

## 抽样与目视参考

新增固定时间点15、45、65、85、95、115秒，共24个区域样本。85秒用于覆盖远处较密集交通；这不是随机样本或最终独立测试。两位助手分别先看无预测框原图并记录，之后再打开程序数量；初始记录和哈希已保留。

本报告中的“目视”均为AI助手视觉参考，不是用户人工真值。每个样本有两份独立观察；两者给出相同精确数时才标“明确”。其余保留两者范围的并集，不把范围当作置信区间。**没有替用户确认任何一个人工标签。**

{chr(10).join(table)}

上表是**未看预测前的初次参考**，其中45和65秒右侧参考后来被纠正。24个区域样本中，初次12个参考为一致明确数值，其中8个与程序一致、4个不同；另12个不确定。**4个初始数值差异不等于4个程序错误。**没有据此报告准确率或总体MAE。

## 连续画面复查后的纠正

| 时间、区域 | 初次助手目视 | 连续画面复查 | 程序 | 结论 |
|---|---|---|---|---|
| 45秒右侧 | 1 | 2–3，倾向3；远端小车临近边界 | 3 | 初次助手漏看灯杆后的公交车及小车，不能判为程序多计 |
| 65秒右侧 | 3 | 4 | 4 | 初次助手漏看灯杆后的真实车辆，程序与复查参考相同 |
| 85秒右侧 | 2 | 2 | 3 | 墙/围栏背景被当成卡车，本帧多计1辆 |
| 95秒右侧 | 4 | 4 | 5 | 墙/围栏背景被当成卡车，本帧多计1辆 |

复查已参考程序框及前后画面，因此仅用于定位原因，不能作为独立评价。45秒公交车被标成car是另一个类别问题；因为仍是一辆真实车辆，它不等于总车辆数多计。两处背景误报也不能解释为整段视频恰好多了两辆独立车辆。

初始目视JSON及哈希保持不变，修正写在独立复查文件和逐项对照的postcomparison字段中。人工确认标签仍为0。

## 已发现的问题

1. **右侧背景误计：**85秒的ID648、95秒的ID778框覆盖固定墙/围栏；各使对应帧右侧数量多1。不是区域归属算错，而是背景先被当成了车辆。上一轮同车双框去重没有解决这种背景误报。
2. **上方远处漏计风险：**65秒程序6、95秒程序3；均低于初次目视范围，但不能据此计算确切漏车数。95秒两处可辨认的小车辆已有低置信检测（约0.20和0.26），位于上方区域内部，未被去重删掉，却没有进入当前输出的跟踪计数。这支持进一步检查跟踪激活/匹配，而不能笼统说YOLO完全没有检测到。只凭选定帧尚不能确定具体拒绝原因；65秒另一个候选更模糊。远处小块、同车重叠框和已进入中央区的车不能全部当成漏检。
3. **区域边界留白：**43.833–44.033秒浅色SUV持续被检测到、ID保持，但底边中点经过两区之间的空隙，6帧（0.20秒）不计入任何区域。按现有规则计算正确，但如果想覆盖连续道路，这个几何范围需要调整。
4. **边界敏感：**97.767–97.833秒部分遮挡车辆的框在边界附近变化，2帧（约0.067秒）暂时归到区外。另有60.367–60.567秒极小目标身份不明，不把它当作确认的真实车辆失败案例。

85秒的具体例子：画面里有两辆真实车辆，但编号648的truck框罩住墙/围栏，所以程序显示3辆。

![85秒右侧：墙和围栏被误计为卡车]({B/'comparison/t085_right_model.jpg'})

边界案例：同一辆浅色SUV从左侧道路驶向中央区。下排小圆点落在黄线和紫线之间时，车辆仍有框，却暂时不属于任何统计区域。

![约44秒的连续画面与分区间隙]({B/'boundary_evidence/case1_spatial_gap_sequence.jpg'})

## 这些结果意味着什么

之前3个时间点中较清楚区域的吻合，不能代表整段视频可靠。本轮发现了背景误报、远处漏计风险和ROI边界问题；这也是先做小片段核对的用途。当前没有证据要求立即重训YOLO，也没有依据直接开始批量115段或交通状态分级。

下一步优先分析右侧背景误报的处理办法，并修订连续道路之间的统计边界；每次修改保存新版本，复查这组案例后再用另外的验证片段检查，避免只对这几个画面有效。远处区域需要单独确定可靠可见范围和误差，不能悄悄删掉困难区域后宣称模型更准。

当前统计的是每帧区域内可见、正在被跟踪的车辆数量，不是累计通过量、实际物理密度或拥堵等级。本轮没有启动其他路口、115段全量或Unity工作，也没有给测试集调参数。

## 文件

- [逐项计数对照]({B/'逐项计数对照.csv'})：24行，含不确定范围和程序差异。
- [人工复核空表]({B/'人工复核填写表.csv'})：人工列全部留空；交互页面也可填写导出。
- [边界问题详细记录]({B/'boundary_review.json'})：3个定点案例及连续帧。
- [右侧复查记录]({B/'postcomparison_right_review.json'})：遮挡车辆与墙/围栏误报。
- [上方复查记录]({B/'postcomparison_top_review.json'})：低置信检测未进入当前跟踪计数的证据及限制。
- [原始参考与核对汇总]({B/'count_comparison.json'})。

'''
    (B/'计数核对报告.md').write_text(report,encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
