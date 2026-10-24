# YOLO11s 正式 Main Test 结果与论文使用说明

**已完成正式检测测试。** 2026-09-28，当前选定的 YOLO11s 最佳权重在全部 184 张独立 Main Test 图片上完成评价。mAP50 = **0.616905**，mAP50–95 = **0.452727**。这组成绩可以用于论文的 held-out detection evaluation。

模型与参数在测试前固定，只有一次模型评价调用；没有根据结果调参、换权重或重训。原始图片、人工标签、模型和既有结果保持不变。A100 补充视频任务没有被本轮操作中断。

## 1. 结果表

| 类别 | 人工标注目标数 | Precision | Recall | AP50 | AP50–95 |
|---|---:|---:|---:|---:|---:|
| 全部类别，宏平均 | 2,052 | 0.749 | 0.531 | **0.617** | **0.453** |
| car | 1,946 | 0.948 | 0.791 | 0.911 | 0.626 |
| motorcycle | 4 | 0.853 | 0.250 | 0.345 | 0.315 |
| bus | 13 | 0.654 | 0.769 | 0.791 | 0.561 |
| truck | 89 | 0.540 | 0.315 | 0.420 | 0.309 |

数值为比例，0.617 可写成 61.7% mAP50，但不能称作“61.7% 的车辆判断正确”。P/R 是 Ultralytics 在平滑平均 F1 最大处取的共同工作点，再对四类平均；不是固定 conf=0.35 的总体 micro P/R，也不把该测试工作点用来重新选择部署阈值。

![YOLO11s Main Test 结果](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28/main_test_summary.png)

图中纵轴为 AP（0–1），横轴为类别与真值目标数；左侧 All classes 为四类等权平均。蓝色为 IoU=0.50 的 AP，橙色为 IoU=0.50–0.95 的平均 AP。

## 2. 怎么解释，和 Validation 有什么关系

| YOLO11s | Validation，历史最终复验 | Main Test，本次 |
|---|---:|---:|
| 图片数 | 197 | 184 |
| mAP50 | 0.707 | 0.617 |
| mAP50–95 | 0.499 | 0.453 |

测试集宏平均 mAP50 较历史验证结果低约 9.0 个百分点，mAP50–95 低约 4.6 个百分点。它们来自不同来源与类别分布；历史 Validation 在 A100 CUDA 上复验，本次为 CPU FP32，不能将差值完全归因于某一个因素，也不能用二者的耗时比较模型速度。

分类别结果说明差异不是所有类别一齐下降：car 的 AP50 为 0.911（历史 Validation 0.907），bus 为 0.791（0.657），motorcycle 为 0.345（0.736），truck 为 0.420（0.529）。motorcycle 在测试集只有 4 个标注，却与 car 一样占宏平均的四分之一，因此总 mAP 必须结合类别结果和样本数解释。bus 也只有 13 个标注，不能据此声称其泛化稳定。

现有证据支持 car 检测较强、truck 和 motorcycle 的检测仍有明显局限。它不证明每一帧或所有车辆都正确；具体错误成因需要逐例检查，不能仅从指标猜测为遮挡或训练不足。本次不因较弱成绩追加调参。

## 3. 本次到底测了什么

- 已选定的 YOLO11s `best.pt`，SHA256 为 `07e2280eda613c5d633196729f8716e9715000430171f49f6341afe573a2f334`。
- 全部 184 张 Main Test，2,052 个框，23 张空标签；17 个测试来源与训练、验证来源隔离。五个路口并不与训练集完全隔离，因此不能写成“未见路口泛化”。
- 原始检测器的全图预测。没有加入 ROI、背景确认、车辆包含框去重、ByteTrack 或交通状态规则。
- Ultralytics 8.4.115；PyTorch 2.13.0；Apple M4 Pro CPU FP32；4 线程；batch=8；imgsz=640；rect=true；conf 下限 0.001；NMS IoU=0.70；max_det=300。实际矩形批次为 384×672，不应写成全部输入都是 640×640。
- AP 匹配使用 IoU=0.50:0.05:0.95。低置信度保留用于计算 PR/AP，没有先按视频运行门槛 0.35 截断。

执行程序总耗时约 20.9 秒，日志中 23 个评价批次约 9.9 秒。原生统计推理为每图约 46.0 ms，仅是本机该检测评估配置下的分阶段耗时，不是完整视频系统端到端实时性能。

标签保持既有 roadway-only 范围，全图预测中未匹配的框可能包括标注范围外的真实停车车辆。因此 FP 不能全部解释成“模型看到了不存在的车辆”。

## 4. 核验情况

1. 运行前逐文件核对历史装配哈希，检测输入采用独立副本，避免原标签目录新增缓存。
2. 运行时检查实际加载图片集合、实际处理 184 张，以及验证器实际统计到的四类目标数，未漏样本。
3. 运行后复核原始图片/标签、实际评价副本、权重和冻结代码/配置；本地完整性记录包含 741 项哈希核查，全部通过。
4. 另一只读复核独立从原生 AP、P、R 数组重算总指标，与报告最大浮点差约 1.11e-16；另外核对原始、准备、运行三份图片/标签，共 1,104 项哈希全部一致。没有为核验重新运行模型。

预测 TXT 有 174 个；10 张没有低分门槛以上的预测，不生成 TXT 是原生工具行为。实际图片处理完整性以验证器的 seen=184、数据集合及样本数为准。

原始图名可能带 `val_batch`，这是工具的固定命名。本次有效参数明确是 `split=test`，这些是 Main Test 图片，而非重新评价 Validation。

## 5. 图表如何用于 meeting

优先展示上面的英文结果总图及 [PR 曲线](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28/run/raw/BoxPR_curve.png)。PR 曲线横轴 Recall、纵轴 Precision；图例为各类 AP50 和总体 mAP50，图注应写清 Main Test、模型和样本数。

原生[混淆矩阵](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28/run/raw/confusion_matrix.png)使用此版本的 conf=0.001、匹配 IoU=0.45，和表中 P/R 的工作点、AP 的匹配范围不同。展示时需带上这两个条件，不能用矩阵直接验证表中 P/R，也不能把它当作视频 conf=0.35 时的表现。

当前没有保存完整 PR 曲线数值数组，因此无法仅由 JSON 精确还原选择的 P/R 置信度点；已保存原生图、AP/P/R 数组与未舍入指标，不影响本次指标有效性，不为此额外重跑。

## 6. 可写入论文的英文结果段落

The selected YOLO11s detector was evaluated on a held-out test set comprising 184 images and 2,052 annotated vehicles from 17 source videos disjoint from the training and validation sources. Using a frozen detection protocol, the model achieved an mAP@0.50 of 0.617 and an mAP@0.50:0.95 of 0.453. Class-specific AP@0.50 values were 0.911 for cars, 0.345 for motorcycles, 0.791 for buses, and 0.420 for trucks. Motorcycle and bus results should be interpreted cautiously because the test set contained only four and thirteen annotated instances, respectively. The evaluation measured detector performance against the existing roadway-only annotations and did not establish the accuracy of tracking, region-level counting, or traffic-state estimation. The split separated source videos rather than intersections.

## 7. 完成后的研究进度

**“最终 Main Test 检测结果尚未生成”这一项现已完成。** 可以把上述表格、图和方法写进论文。交通状态尚无独立真值的情况仍然存在，需要按既定不新增标注约束与老师讨论评价范围；本次检测测试没有替代状态评价。

## 8. 文件入口

- [未舍入结果 JSON](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28/run/metrics.json) · [结果 CSV](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28/main_test_metrics.csv)
- [运行前固定方案](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28/冻结方案与评估范围.md) · [冻结配置](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28/preparation/evaluation_config.json)
- [实际运行参数](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28/run/effective_runtime.json) · [运行日志](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28/execution.log)
- [原始指标数组](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28/run/metrics_arrays.json) · [运行后完整性检查](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/evaluation/yolo11s_main_test_v1_2026-09-28/post_run_integrity.json)
- [正式评估程序](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/src/evaluate_main_test.py)。本次执行版本已另存 preparation 并封存 SHA，不能复用现有输出目录运行。
