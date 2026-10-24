# NE8th：原与补充来源对比

**简短结论：两段均为约一小时Validation且配置/ROI相同，但日夜观察数量与运动证据覆盖差距极大；这是最清楚的方法边界案例。**

原源：`Bellevue_Bellevue_NE8th__2017-09-11_12-08-31.mp4`，Validation，3600.003秒。

补充：`Bellevue_Bellevue_NE8th__2017-09-11_02-08-29.mp4`，Validation，3600.017秒。

合计均值32.210→0.266、中位数32→0，补充夜间75.76%显示时间为选区零检测。road_top 为14.782→0.132。此差异只证明模型输出不同，不足以区分真实需求降低与夜间漏检等因素。

下表均为原 → 补充；均值单位是辆，P10/中位数/P90是按有效原显示时长加权的逐帧当前数量分布，不是10秒均值的分位数。

| 同名区域 | 加权均值 | 原 P10 / 中位数 / P90 | 补充 P10 / 中位数 / P90 | 零检测时间 % |
| --- | --- | --- | --- | --- |
| road_left | 5.082 → 0.061 | 2 / 5 / 8 | 0 / 0 / 0 | 0.7 → 94.1 |
| road_top | 14.782 → 0.132 | 6 / 15 / 23 | 0 / 0 / 1 | 0.1 → 87.8 |
| road_right | 10.169 → 0.037 | 3 / 10 / 18 | 0 / 0 / 0 | 0.4 → 96.4 |
| junction_center | 2.177 → 0.036 | 0 / 2 / 4 | 0 / 0 / 0 | 14.4 → 96.6 |
| TOTAL_SELECTED_ROIS | 32.210 → 0.266 | 23 / 32 / 41 | 0 / 0 / 1 | 0.0 → 75.8 |

完整曲线、分布：[英文数量图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/NE8th_counts_and_distribution.png)；[PDF](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/NE8th_counts_and_distribution.pdf)。

**运动证据。** 补充夜间四区未知时间分别95.3%、90.6%、98.1%、100%；junction_center没有可用运动。少量被检测并持续跟踪的车辆形成条件样本，因此夜间道路区中位数较高不能证明真实道路速度更高或更畅通。

以下中位数只来自满足证据条件的10秒窗口；可用时间分母是该来源的全部原PTS时长，余下时间均为未知，含尾窗、保持、无观察和运动不足。

| 区域 | 运动中位数 px/s 原→补充 | 可用原时间 % 原→补充 | 可用窗口数 原→补充 |
| --- | --- | --- | --- |
| road_left | 0.62 → 25.40 | 99.7 → 4.7 | 359 → 17 |
| road_top | 1.30 → 15.60 | 100.0 → 9.4 | 360 → 34 |
| road_right | 1.16 → 11.18 | 99.2 → 1.9 | 357 → 7 |
| junction_center | 142.82 → 未知 | 84.7 → 0.0 | 305 → 0 |

[英文运动证据图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/NE8th_motion_evidence.png)

**缺失与保持。** 两段原记录缺失均为0秒；长保持为0段/0.000秒 → 0段/0.000秒，影响0 → 0个10秒窗。合计均值在剔除整段长保持帧显示区间后为32.210 → 0.266。此为时间权重敏感性，不是对真实车辆数的校正。

**可比性与局限。** 日间远端车辆密集且有遮挡，夜间照明不均和车灯眩光明显。两段均无>0.5秒PTS保持，也不代表夜间证据充分。所有正常间隔的像素重复尚未做全视频画面审计。

图像证据：[原/补充并排图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/evidence/NE8th_pair.png)。约60秒位置用来说明场景、ROI和光照，不宣称代表全来源；两个来源也未对齐信号周期。

**可回看的定量示例区间。** 以下按每10秒滑动、连续六个完整无保持窗口自动选出选区合计均值最低与最高的60秒；这是探索性极值，不是随机样本，也不能当总体代表或独立检验。

| 来源 | 较低观察60秒（PTS秒）／均值 | 较高观察60秒（PTS秒）／均值 |
| --- | --- | --- |
| Original (Validation) | 1060–1120 / 19.458 | 2450–2510 / 43.894 |
| Supplementary (Validation) | 120–180 / 0.000 | 3500–3560 / 1.449 |

已有补充短预览：[观看](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_five_2026-09-28_v3_repair/delivery/Bellevue_Bellevue_NE8th/preview.mp4)，覆盖原PTS 60.000744–90.001100秒；播放器0秒对应上述原PTS起点。短预览没有覆盖上述全部高低区间。全部精确帧号/PTS/哈希见视觉证据元数据。
