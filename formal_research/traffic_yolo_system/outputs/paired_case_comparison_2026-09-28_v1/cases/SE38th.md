# SE38th：原与补充来源对比

**简短结论：同名区域观察均值均较低，补充源后半段较前半段低；原源长保持帧明显更多，但去除保持区间后不改变均值差异方向。**

原源：`Bellevue_150th_SE38th__2017-09-11_14-08-32.mp4`，Validation，3600.030秒。

补充：`Bellevue_150th_SE38th__2017-09-10_19-08-25.mp4`，Validation，3600.010秒。

原源最后一刻钟均值19.262，高于前三段；补充四分段为7.503、7.250、5.846、6.297，不能描述成严格单调下降。合计中位数16→7，P90为23→11。

下表均为原 → 补充；均值单位是辆，P10/中位数/P90是按有效原显示时长加权的逐帧当前数量分布，不是10秒均值的分位数。

| 同名区域 | 加权均值 | 原 P10 / 中位数 / P90 | 补充 P10 / 中位数 / P90 | 零检测时间 % |
| --- | --- | --- | --- | --- |
| road_left | 4.351 → 1.814 | 1 / 4 / 8 | 0 / 2 / 4 | 4.2 → 20.5 |
| road_top | 5.953 → 1.892 | 1 / 6 / 11 | 0 / 2 / 5 | 4.9 → 24.3 |
| road_right | 4.514 → 2.237 | 1 / 4 / 9 | 0 / 2 / 5 | 5.6 → 21.4 |
| junction_center | 1.396 → 0.781 | 0 / 1 / 3 | 0 / 0 / 2 | 29.3 → 51.0 |
| TOTAL_SELECTED_ROIS | 16.214 → 6.724 | 10 / 16 / 23 | 3 / 7 / 11 | 0.0 → 0.5 |

完整曲线、分布：[英文数量图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/SE38th_counts_and_distribution.png)；[PDF](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/SE38th_counts_and_distribution.pdf)。

**运动证据。** road_top 条件运动中位数1.29→6.69 px/s，可用时间89.2%→74.4%；road_left 11.81→30.33，road_right 32.12→31.16，仍存在方向及证据组成差异。

以下中位数只来自满足证据条件的10秒窗口；可用时间分母是该来源的全部原PTS时长，余下时间均为未知，含尾窗、保持、无观察和运动不足。

| 区域 | 运动中位数 px/s 原→补充 | 可用原时间 % 原→补充 | 可用窗口数 原→补充 |
| --- | --- | --- | --- |
| road_left | 11.81 → 30.33 | 87.8 → 80.3 | 316 → 289 |
| road_top | 1.29 → 6.69 | 89.2 → 74.4 | 321 → 268 |
| road_right | 32.12 → 31.16 | 88.9 → 76.1 | 320 → 274 |
| junction_center | 124.38 → 154.43 | 49.7 → 35.3 | 179 → 127 |

[英文运动证据图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/SE38th_motion_evidence.png)

**缺失与保持。** 两段原记录缺失均为0秒；长保持为18段/98.733秒 → 2段/4.967秒，影响24 → 2个10秒窗。合计均值在剔除整段长保持帧显示区间后为16.077 → 6.723。此为时间权重敏感性，不是对真实车辆数的校正。

**可比性与局限。** 原白天与补充黄昏/入夜，补充源首尾照明变暗；前景设备遮挡未消失。原18个长保持帧占98.733秒，补充2个占4.967秒；保持期间不是新的图像证据，更不能视为车辆停止。

图像证据：[原/补充并排图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/evidence/SE38th_pair.png)。约60秒位置用来说明场景、ROI和光照，不宣称代表全来源；两个来源也未对齐信号周期。

**可回看的定量示例区间。** 以下按每10秒滑动、连续六个完整无保持窗口自动选出选区合计均值最低与最高的60秒；这是探索性极值，不是随机样本，也不能当总体代表或独立检验。

| 来源 | 较低观察60秒（PTS秒）／均值 | 较高观察60秒（PTS秒）／均值 |
| --- | --- | --- |
| Original (Validation) | 1680–1740 / 8.862 | 1210–1270 / 26.765 |
| Supplementary (Validation) | 3130–3190 / 1.515 | 580–640 / 12.700 |

已有补充短预览：[观看](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_five_2026-09-28_v3_repair/delivery/Bellevue_150th_SE38th/preview.mp4)，覆盖原PTS 60.033167–90.033067秒；播放器0秒对应上述原PTS起点。短预览没有覆盖上述全部高低区间。全部精确帧号/PTS/哈希见视觉证据元数据。

[补充源首尾光照原图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/evidence/SE38th_light_change.png)
