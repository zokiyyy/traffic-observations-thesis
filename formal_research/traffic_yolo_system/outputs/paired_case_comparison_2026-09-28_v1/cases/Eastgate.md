# Eastgate：原与补充来源对比

**简短结论：补充源总体观察数量较低，但源内四个约15分钟均值逐步上升；它最适合展示“完整来源的趋势比单一均值更有信息”。**

原源：`Bellevue_150th_Eastgate__2017-09-11_17-08-33.mp4`，Validation，3599.991秒。

补充：`Bellevue_150th_Eastgate__2017-09-11_06-08-30.mp4`，Validation，3600.004秒。

原源合计四分段均值23.355→22.057→21.839→19.831；补充源2.894→3.330→5.973→7.778。road_top 均值9.270→1.805、road_upper_left 6.234→1.200，变化需按同名区域分别解读。

下表均为原 → 补充；均值单位是辆，P10/中位数/P90是按有效原显示时长加权的逐帧当前数量分布，不是10秒均值的分位数。

| 同名区域 | 加权均值 | 原 P10 / 中位数 / P90 | 补充 P10 / 中位数 / P90 | 零检测时间 % |
| --- | --- | --- | --- | --- |
| road_left | 1.872 → 0.131 | 0 / 2 / 4 | 0 / 0 / 1 | 14.0 → 88.0 |
| road_upper_left | 6.234 → 1.200 | 3 / 6 / 10 | 0 / 1 / 3 | 2.3 → 36.4 |
| road_top | 9.270 → 1.805 | 4 / 10 / 14 | 0 / 1 / 4 | 0.0 → 26.7 |
| road_right | 2.385 → 1.230 | 1 / 2 / 4 | 0 / 1 / 3 | 9.0 → 32.7 |
| junction_center | 2.009 → 0.628 | 0 / 2 / 4 | 0 / 0 / 2 | 13.9 → 54.3 |
| TOTAL_SELECTED_ROIS | 21.770 → 4.994 | 15 / 22 / 28 | 1 / 5 / 10 | 0.0 → 4.0 |

完整曲线、分布：[英文数量图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/Eastgate_counts_and_distribution.png)；[PDF](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/Eastgate_counts_and_distribution.pdf)。

**运动证据。** road_top 的条件运动中位数1.45→10.41 px/s，可用时间98.9%→71.1%；补充road_left没有一个窗口满足运动门槛。数量与运动可以共同描述证据，却不能自动产生真实排队或拥堵判断。

以下中位数只来自满足证据条件的10秒窗口；可用时间分母是该来源的全部原PTS时长，余下时间均为未知，含尾窗、保持、无观察和运动不足。

| 区域 | 运动中位数 px/s 原→补充 | 可用原时间 % 原→补充 | 可用窗口数 原→补充 |
| --- | --- | --- | --- |
| road_left | 51.00 → 未知 | 53.9 → 0.0 | 194 → 0 |
| road_upper_left | 1.26 → 0.83 | 94.7 → 55.3 | 341 → 199 |
| road_top | 1.45 → 10.41 | 98.9 → 71.1 | 356 → 256 |
| road_right | 9.00 → 9.07 | 87.8 → 62.8 | 316 → 226 |
| junction_center | 120.70 → 127.46 | 62.2 → 24.4 | 224 → 88 |

[英文运动证据图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/Eastgate_motion_evidence.png)

**缺失与保持。** 两段原记录缺失均为0秒；长保持为0段/0.000秒 → 0段/0.000秒，影响0 → 0个10秒窗。合计均值在剔除整段长保持帧显示区间后为21.770 → 4.994。此为时间权重敏感性，不是对真实车辆数的校正。

**可比性与局限。** 原为傍晚较亮画面，补充由黎明转为日光，源首尾原图证实照明变化；因此补充源内数量上升也可能同时受可检出性改善影响。约60秒补充样例选区0、ROI外2，直接说明“选区0”不等于整个画面无车。部分道路没有纳入ROI。

图像证据：[原/补充并排图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/evidence/Eastgate_pair.png)。约60秒位置用来说明场景、ROI和光照，不宣称代表全来源；两个来源也未对齐信号周期。

**可回看的定量示例区间。** 以下按每10秒滑动、连续六个完整无保持窗口自动选出选区合计均值最低与最高的60秒；这是探索性极值，不是随机样本，也不能当总体代表或独立检验。

| 来源 | 较低观察60秒（PTS秒）／均值 | 较高观察60秒（PTS秒）／均值 |
| --- | --- | --- |
| Original (Validation) | 1870–1930 / 13.020 | 2250–2310 / 29.868 |
| Supplementary (Validation) | 210–270 / 0.716 | 2740–2800 / 12.974 |

已有补充短预览：[观看](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_five_2026-09-28_v3_repair/delivery/Bellevue_150th_Eastgate/preview.mp4)，覆盖原PTS 60.033211–90.033167秒；播放器0秒对应上述原PTS起点。短预览没有覆盖上述全部高低区间。全部精确帧号/PTS/哈希见视觉证据元数据。

[补充源首尾光照原图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/evidence/Eastgate_light_change.png)

[补充较低/较高60秒区间内的原图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/evidence/Eastgate_analysis_interval_context.png)：实际原PTS 240.032900秒与2770.005189秒。原图保留原像素，无新推理或人工计数；分别只是所选区间中的一个时刻。
