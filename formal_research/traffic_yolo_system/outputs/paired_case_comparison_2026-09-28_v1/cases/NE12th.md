# NE12th：原与补充来源对比

**简短结论：这是夜间Validation与短白天Train的观察条件对照，只适合说明证据可得性差异，不能用作两段等长独立验证或“白天更拥堵”的结论。**

原源：`Bellevue_116th_NE12th__2017-09-10_23-08-29.mp4`，Validation，3600.010秒。

补充：`Bellevue_116th_NE12th__2017-09-11_07-08-32.mp4`，Train，408.577秒。

原夜间各区域P90均为0，选区合计75.08%的原显示时间为零检测；补充白天的合计中位数8、P90为11。补充仅408.577秒，不能把7.405与原一小时0.303的差值当作日夜交通量效应。

下表均为原 → 补充；均值单位是辆，P10/中位数/P90是按有效原显示时长加权的逐帧当前数量分布，不是10秒均值的分位数。

| 同名区域 | 加权均值 | 原 P10 / 中位数 / P90 | 补充 P10 / 中位数 / P90 | 零检测时间 % |
| --- | --- | --- | --- | --- |
| road_left | 0.049 → 1.285 | 0 / 0 / 0 | 0 / 1 / 4 | 95.2 → 37.7 |
| road_top | 0.104 → 2.416 | 0 / 0 / 0 | 0 / 2 / 6 | 90.5 → 25.3 |
| road_right | 0.079 → 2.167 | 0 / 0 / 0 | 0 / 2 / 5 | 93.0 → 14.0 |
| road_bottom | 0.027 → 0.848 | 0 / 0 / 0 | 0 / 1 / 2 | 97.3 → 44.9 |
| junction_center | 0.045 → 0.690 | 0 / 0 / 0 | 0 / 0 / 2 | 95.8 → 54.3 |
| TOTAL_SELECTED_ROIS | 0.303 → 7.405 | 0 / 0 / 1 | 3 / 8 / 11 | 75.1 → 0.3 |

完整曲线、分布：[英文数量图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/NE12th_counts_and_distribution.png)；[PDF](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/NE12th_counts_and_distribution.pdf)。

**运动证据。** 原夜间各区运动可用时间仅0%—6.1%，junction_center完全未知；白天Train可用比例提高，但区域间仍为17.1%—78.3%。原夜间仅2至22个可用道路窗口，条件中位数没有稳定对照基础。

以下中位数只来自满足证据条件的10秒窗口；可用时间分母是该来源的全部原PTS时长，余下时间均为未知，含尾窗、保持、无观察和运动不足。

| 区域 | 运动中位数 px/s 原→补充 | 可用原时间 % 原→补充 | 可用窗口数 原→补充 |
| --- | --- | --- | --- |
| road_left | 13.30 → 1.23 | 0.6 → 39.2 | 2 → 16 |
| road_top | 14.00 → 3.91 | 6.1 → 58.7 | 22 → 24 |
| road_right | 7.44 → 9.46 | 2.5 → 78.3 | 9 → 32 |
| road_bottom | 15.86 → 5.73 | 1.7 → 46.5 | 6 → 19 |
| junction_center | 未知 → 156.95 | 0.0 → 17.1 | 0 → 7 |

[英文运动证据图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/figures/NE12th_motion_evidence.png)

**缺失与保持。** 两段原记录缺失均为0秒；长保持为13段/48.701秒 → 3段/14.200秒，影响19 → 4个10秒窗。合计均值在剔除整段长保持帧显示区间后为0.299 → 7.376。此为时间权重敏感性，不是对真实车辆数的校正。

**可比性与局限。** 同一1280×720机位标识、同ROI坐标与数值参数得到核对，但日夜眩光/低照度、来源划分、时长和不同日期共同限制解释。补充Train身份贯穿全部表格、图例和追溯文件；不做准确率、迁移成功或外推验证表述。

图像证据：[原/补充并排图](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/evidence/NE12th_pair.png)。约60秒位置用来说明场景、ROI和光照，不宣称代表全来源；两个来源也未对齐信号周期。

**可回看的定量示例区间。** 以下按每10秒滑动、连续六个完整无保持窗口自动选出选区合计均值最低与最高的60秒；这是探索性极值，不是随机样本，也不能当总体代表或独立检验。

| 来源 | 较低观察60秒（PTS秒）／均值 | 较高观察60秒（PTS秒）／均值 |
| --- | --- | --- |
| Original (Validation) | 360–420 / 0.000 | 1450–1510 / 1.312 |
| Supplementary (Train) | 120–180 / 2.778 | 200–260 / 11.052 |

已有补充短预览：[观看](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_five_2026-09-28_v3_repair/delivery/Bellevue_116th_NE12th/preview.mp4)，覆盖原PTS 60.001489–90.002244秒；播放器0秒对应上述原PTS起点。短预览没有覆盖上述全部高低区间。全部精确帧号/PTS/哈希见视觉证据元数据。
