# Newport：原与补充来源对比

**简短结论：四个同名区域的模型平均当前数量均下降，但保留各自短时起伏；这一结果支持选区观察对比，不能直接归因于早晚时段。**

原源：`Bellevue_150th_Newport__2017-09-11_08-08-31.mp4`，Validation，3600.015秒。

补充：`Bellevue_150th_Newport__2017-09-10_18-08-24.mp4`，Validation，3600.031秒。

road_right 从3.734降至1.040，road_top 从5.341降至3.124。选区合计的时间加权中位数由12变为5，P90由17变为10；补充源第一刻钟较高，后三刻钟约5.4，并非整段持续下降。

下表均为原 → 补充；均值单位是辆，P10/中位数/P90是按有效原显示时长加权的逐帧当前数量分布，不是10秒均值的分位数。

| 同名区域 | 加权均值 | 原 P10 / 中位数 / P90 | 补充 P10 / 中位数 / P90 | 零检测时间 % |
| --- | --- | --- | --- | --- |
| road_left | 1.765 → 0.741 | 0 / 2 / 3 | 0 / 0 / 2 | 15.1 → 50.2 |
| road_top | 5.341 → 3.124 | 2 / 5 / 9 | 0 / 3 / 6 | 1.7 → 10.3 |
| road_right | 3.734 → 1.040 | 1 / 3 / 7 | 0 / 1 / 3 | 6.6 → 41.5 |
| junction_center | 1.436 → 0.798 | 0 / 1 / 3 | 0 / 1 / 2 | 20.2 → 45.4 |
| TOTAL_SELECTED_ROIS | 12.276 → 5.704 | 7 / 12 / 17 | 2 / 5 / 10 | 0.1 → 0.8 |

完整曲线、分布：[英文数量图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/Newport_counts_and_distribution.png)；[PDF](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/Newport_counts_and_distribution.pdf)。

**运动证据。** road_right 的条件运动中位数5.38→9.82 px/s，但可用时间比例93.3%→48.1%；road_top 则8.44→7.24 px/s。方向并不一致，不能概括成“新源更快”。

以下中位数只来自满足证据条件的10秒窗口；可用时间分母是该来源的全部原PTS时长，余下时间均为未知，含尾窗、保持、无观察和运动不足。

| 区域 | 运动中位数 px/s 原→补充 | 可用原时间 % 原→补充 | 可用窗口数 原→补充 |
| --- | --- | --- | --- |
| road_left | 1.79 → 1.84 | 79.7 → 41.7 | 287 → 150 |
| road_top | 8.44 → 7.24 | 98.9 → 90.3 | 356 → 325 |
| road_right | 5.38 → 9.82 | 93.3 → 48.1 | 336 → 173 |
| junction_center | 97.11 → 92.24 | 60.3 → 38.1 | 217 → 137 |

[英文运动证据图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/Newport_motion_evidence.png)

**缺失与保持。** 两段原记录缺失均为0秒；长保持为0段/0.000秒 → 0段/0.000秒，影响0 → 0个10秒窗。合计均值在剔除整段长保持帧显示区间后为12.276 → 5.704。此为时间权重敏感性，不是对真实车辆数的校正。

**可比性与局限。** 原源为9月11日早间，补充为9月10日傍晚，日期及照明同时变化。前景设备遮挡、远端小目标和区域尺度限制持续存在。Newport背景二次分辨率确认在两段中均启用，数值规则相同；新源适用性只有稀疏图像审阅支持，过滤准确率没有独立验证。

图像证据：[原/补充并排图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/evidence/Newport_pair.png)。约60秒位置用来说明场景、ROI和光照，不宣称代表全来源；两个来源也未对齐信号周期。

**可回看的定量示例区间。** 以下按每10秒滑动、连续六个完整无保持窗口自动选出选区合计均值最低与最高的60秒；这是探索性极值，不是随机样本，也不能当总体代表或独立检验。

| 来源 | 较低观察60秒（PTS秒）／均值 | 较高观察60秒（PTS秒）／均值 |
| --- | --- | --- |
| Original (Validation) | 960–1020 / 4.647 | 0–60 / 21.081 |
| Supplementary (Validation) | 2890–2950 / 2.817 | 3210–3270 / 10.798 |

已有补充短预览：[观看](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_five_2026-09-28_v3_repair/delivery/Bellevue_150th_Newport/preview.mp4)，覆盖原PTS 60.000156–90.000233秒；播放器0秒对应上述原PTS起点。短预览没有覆盖上述全部高低区间。全部精确帧号/PTS/哈希见视觉证据元数据。
