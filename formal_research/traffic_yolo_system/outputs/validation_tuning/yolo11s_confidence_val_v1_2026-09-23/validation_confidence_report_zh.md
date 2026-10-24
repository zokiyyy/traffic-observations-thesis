# YOLO11s 三种检测门槛验证结果

完成日期：2026-09-23。使用全部 **197 张 Validation 图片**和原有人工标注；未重新训练，未读取或评估 Main Test 图片。

## 结论和下一步起点

在本次比较的 **0.20、0.25、0.35** 三组中，**0.35 的总体固定门槛 F1 最高，整图框数误差最低**。建议把 **conf=0.35、imgsz=640、NMS IoU=0.70** 作为下一步小视频的**检测候选设置**，保留 0.25 的结果作为对照。不再继续无边界搜索更多门槛。

这个结论有明确取舍：相比 0.25，0.35 减少了 **139 个相对人工标注未匹配的预测框**，同时减少 **34 个匹配成功的车辆标注**。其中 car 少 29 个、motorcycle 少 2 个、truck 少 3 个，bus 不变。因此不能称为所有类别、所有场景都更好。

这里选择的是检测候选门槛。**没有把 ByteTrack 的输入门槛直接改成 0.35**：跟踪器可能需要较低分框来维持轨迹，后续接入时应分别确定“交给跟踪器的检测保留条件”和“用于显示/统计的筛选条件”。本次没有运行跟踪，也没有冻结最终交通分析方案。

## 全部 197 张图片的结果

匹配成功要求：**类别相同、预测框与人工框 IoU≥0.50、同一框只匹配一次**。先汇总全部图片的 TP/FP/FN，再计算 micro Precision、Recall 和 F1。NMS IoU=0.70 是另一个用于生成预测的参数。

| conf | 预测框数 | 匹配成功 TP | 未匹配预测 FP | 未匹配标注 FN | Precision | Recall | F1 | 整图数量 MAE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.20 | 2058 | 1544 | 514 | 192 | 75.02% | 88.94% | 81.39% | 1.807 |
| 0.25 | 1973 | 1529 | 444 | 207 | 77.50% | 88.08% | 82.45% | 1.426 |
| 0.35 | 1800 | 1495 | 305 | 241 | 83.06% | 86.12% | 84.56% | 0.914 |

- Precision：保留下来的预测框里，有多少与人工标注正确匹配。
- Recall：人工标注的车辆里，有多少被正确匹配。
- F1：Precision 与 Recall 的综合指标；本次是固定门槛下的总体指标。
- 整图数量 MAE：每张图的预测框总数与标注总数之差的绝对值，再对 197 张图平均。它不区分类别、可能发生误检漏检相互抵消，也不等于道路区域计数、独立车辆流量、密度或拥堵准确性。
- 0.35 相比 0.25：Precision 提高约 **5.56 个百分点**，Recall 下降约 **1.96 个百分点**，F1 提高约 **2.11 个百分点**。
- 0.20 相比 0.25：多匹配 15 个标注，但增加 70 个未匹配预测框，总体 F1 较低。

## 类别取舍

下表每格为“匹配成功个数 / Recall”。

| 类别 | 人工标注数 | conf 0.20 | conf 0.25 | conf 0.35 |
|---|---:|---:|---:|---:|
| car | 1621 | 1478 / 91.18% | 1463 / 90.25% | 1434 / 88.46% |
| motorcycle | 8 | 6 / 75.00% | 6 / 75.00% | 4 / 50.00% |
| bus | 22 | 16 / 72.73% | 16 / 72.73% | 16 / 72.73% |
| truck | 85 | 44 / 51.76% | 44 / 51.76% | 41 / 48.24% |

car 占 1,736 个标注中的 1,621 个（93.4%），总体 micro 指标主要反映 car。四类等权平均 F1 从 0.25 的 **68.91%** 下降到 0.35 的 **67.03%**。如果研究的首要目标变为每一小类同等重要，0.35 不能直接称作优选。

motorcycle 仅 8 个标注，0.35 下由匹配 6 个变成 4 个；truck 的匹配数由 44 个变成 41 个。本次保留全部标签，不为改善某一门槛的结果修改类别或人工标注。

## 场景分组

分组来自已冻结来源元数据，Lighting 不是每张图片的交通状态真值。

| 光照 | 图片数 | 标注车辆数 | 0.20 F1 | 0.25 F1 | 0.35 F1 |
|---|---:|---:|---:|---:|---:|
| Day | 77 | 1422 | 80.23% | 81.54% | 84.05% |
| Dawn/Dusk | 24 | 160 | 85.55% | 85.71% | 86.15% |
| Night | 96 | 154 | 88.67% | 87.95% | 87.71% |

Night 图片较多，但车辆标注较少，全部 46 张空标签图片也在 Night。不能因为 Night 的总体 F1 较高，就宣布夜间问题已经解决；0.35 的 Night F1 比 0.25 略低。

| 路口 | 图片数 | 0.20 F1 | 0.25 F1 | 0.35 F1 |
|---|---:|---:|---:|---:|
| Bellevue_116th_NE12th | 41 | 83.39% | 85.33% | 88.73% |
| Bellevue_150th_Eastgate | 36 | 79.82% | 80.00% | 82.06% |
| Bellevue_150th_Newport | 36 | 78.47% | 80.31% | 83.40% |
| Bellevue_150th_SE38th | 48 | 82.48% | 83.68% | 84.66% |
| Bellevue_Bellevue_NE8th | 36 | 84.56% | 85.51% | 87.22% |

0.35 对比 0.25，五个路口分组的 F1 均提高；细分到 17 个来源视频，**10 个提高、3 个下降、4 个相同**。这是已有验证样本上的描述性结果，不是统计显著性或独立泛化结论。

## 空标签图片

全部 46 张合法空标签图片均参加评估，未删除困难图片。

| conf | 空标签图片上的预测框数 | 至少出现一个预测框的空标签图片 |
|---|---:|---:|
| 0.20 | 2 | 2 / 46 |
| 0.25 | 2 | 2 / 46 |
| 0.35 | 1 | 1 / 46 |

## 结果解释边界

1. 原人工标注采用 roadway-only 范围，排除了停车区等非道路车辆。本次预测覆盖整张图片，没有新加 ROI 或忽略区。因此 FP 是“相对现有标注未匹配的预测”，不能全部解释成模型看到了不存在的车辆。
2. FN 表示未按本次“同类别、IoU≥0.50”规则匹配成功；错类或框太小也会产生 FN，不一定完全没检测到车辆。
3. 本次不是重新计算 mAP。训练报告的最终验证 P/R 采用不同聚合及工作点口径，不能把这里的 83.06%/86.12% 与旧报告的 0.709/0.695 直接相减，声称模型训练能力提升。权重未改变。
4. 匹配采用已明确记录的逐对 IoU 降序贪心一对一方法；不保证最大二分匹配数量，也不声称与 Ultralytics 全部内部匹配细节完全一致。
5. 检测结果不直接证明跟踪、区域计数或交通状态正确。下一步仍是把这份检测候选设置接入已有小视频处理流程，检查连续性；本次没有启动 115 段视频批处理或 Unity。

## 复现信息与核验

- 模型：已经训练好的 `yolo11s_formal_a100_v1/weights/best.pt`。
- 模型 SHA256：`07e2280eda613c5d633196729f8716e9715000430171f49f6341afe573a2f334`。
- 三个门槛分别实际调用 predict；每张图的输入、权重、640、NMS IoU=0.70、FP32、类别相关 NMS、max_det=300、无增强均保持一致。
- A100 CUDA；Ultralytics 8.4.115；PyTorch 2.13.0+cu130。没有 CPU 回退。
- 全部图片和标签在运行前与本地冻结清单逐个 SHA256 核对。197 图、197 标签、17 个 Validation 来源，无 Main Test 来源；46 个空标签保留。
- 程序匹配与汇总经过独立合成案例、随机 IoU 矩阵检查；实际结果另按独立实现重算。核验文件见 `job/`。
- 完整配置：`results/config.json`；统计：`results/summary.json`；逐图框及匹配：`results/predictions_and_matches.jsonl`；运行状态：`results/status.json`；日志：`job/run.log`。
- 检测候选选择与限制：`selection.json`。原训练配置和历史结果没有覆盖。

## 可视化检查

![三个门槛的 Precision、Recall 和 F1 对比](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/validation_tuning/yolo11s_confidence_val_v1_2026-09-23/review/confidence_micro_metrics.png)

下列对照图左侧为人工标注，中间为 0.25，右侧为 0.35。青色是人工框，绿色是同类别匹配成功的预测，橙色是未匹配预测。样例用于解释取舍，不替代全部 197 张的统计。

| 样例 | 0.25 → 0.35 的变化 | 查看图片 |
|---|---|---|
| F1018：减少多余框、正确匹配不变 | TP 18→18，FP 3→0 | [完整对照](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/validation_tuning/yolo11s_confidence_val_v1_2026-09-23/review/05_reduction_without_tp_loss.jpg) |
| F0480：减少较多未匹配框 | TP 34→33，FP 10→4 | [完整对照](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/validation_tuning/yolo11s_confidence_val_v1_2026-09-23/review/01_maximum_fp_reduction.jpg) |
| F0178：摩托车被过滤 | TP 25→24，FP 7→5；摩托车预测分数约 0.27 | [摩托车局部放大](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/validation_tuning/yolo11s_confidence_val_v1_2026-09-23/review/06_motorcycle_crop.jpg) · [完整对照](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/validation_tuning/yolo11s_confidence_val_v1_2026-09-23/review/02_motorcycle_tradeoff.jpg) |
| F0794：卡车的正确类别框被过滤 | TP 4→3，FP 1→1 | [完整对照](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/validation_tuning/yolo11s_confidence_val_v1_2026-09-23/review/03_truck_tradeoff.jpg) |
| F0226：夜间的一个改善例子 | TP 4→4，FP 1→0；不代表全部夜间都改善 | [完整对照](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/validation_tuning/yolo11s_confidence_val_v1_2026-09-23/review/04_night_change.jpg) |

F0794 中消失的是分数约 0.28 的 truck 框，同一车辆位置仍保留约 0.73 的错误 car 框。因此这里是**正确类别匹配丢失**，不能说整辆车完全没检测到。F0178 的局部图则可见摩托车框确实被较高门槛过滤。

[样例索引](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/validation_tuning/yolo11s_confidence_val_v1_2026-09-23/review/index.json)记录选择依据；[视觉核验记录](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/validation_tuning/yolo11s_confidence_val_v1_2026-09-23/review/visual_qa.json)记录图片检查结果。[独立统计复核](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/validation_tuning/yolo11s_confidence_val_v1_2026-09-23/job/independent_results_verification.json)核对了 76,412 个标量字段，差异为 0。
