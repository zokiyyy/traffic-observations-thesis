# 独立核验结论

**全部已落地的五段派生数据通过独立核验，未发现未解决的数值或门控错误。** 本审查不读原逐帧日志或视频，不修改原运动数据、Main Test 或 Unity。

## 覆盖与结果

- 5 个补充来源、22 个区域、6,342 个区域窗口全部逐行核验。
- 22 套数量 P33/P67、运动 P25、校准样本数及校准窗口索引，均由独立代码重新计算。独立核验程序不导入冻结引擎或生产派生函数。
- 每窗原数量与绝对运动字段逐值保持不变；相对数量等级、冻结 `state_hint`、高数量与联合持续时长、综合显示、unknown 原因及首次确认位置全部匹配独立重算。
- 247 个提示事件的条件起点、首次/末次确认、事件窗口数和综合未知窗口数均匹配；全部区域汇总、来源索引、CSV、示例选择均核对通过。
- 3,674 个无绝对运动证据的区域窗保留 `motion_median_px_s=null`。90 个有绝对运动观察但运动 P25 校准不足的区域窗保留绝对数值、`motion_low_relative=null`、`evidence_state=unknown`，正确区分观察不足与校准不足。
- 41 个区域窗有纯数量 30 秒持续提示但综合证据不足；其纯数量布尔值为 true，综合显示保持 unknown，未被冻结 `high_count_persistent` 优先分支覆盖。
- 联合提示是高数量提示的子集，`high_count_persistent_count_only` 为 true 不表示与 joint 互斥，不能将两个提示窗口数相加当作不重复事件量。

NE12th 保留 Train 与 408.5768556 秒身份；只有 road_right 达到运动 P25 的 30 个校准窗要求。NE8th 四区 P33/P67 都是 0，所有综合显示均 unknown；Eastgate road_left 也因运动不足全部 unknown。各来源自校准结果不构成外推验证，不能跨来源比较等级或提示比例。

## 边界测试

`boundary_tests.json`：冻结引擎 19 项边界测试全部通过。

`display_boundary_tests.json`：实际生产 `add_display` 的 11 项边界测试全部通过。

共 30 项，覆盖线性插值、29/30 个运动校准样本、数量阈值重合及等号、20/30 秒持续门槛、保持/缺失/尾窗/不连续 PTS、运动缺失仅重置联合计时、零观察语义、综合未知覆盖、绝对运动与相对校准缺失的区别、首次确认、联合提示为数量提示子集，以及 NE12th Train 身份。合成边界测试只验证实现，不评估交通准确率。

## 复现与记录

```sh
python3 -B /Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/test_frozen_boundaries.py
python3 -B /Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/test_display_boundaries.py
python3 -B /Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/verify_derived.py
```

`independent_verification.json` 保存全部读取的小文件哈希、逐来源核验结果和核验脚本哈希；`expected_thresholds.json` 保存独立阈值计算结果。`method_review.md` 保留派生前的方法审查。复现只写本 verification 目录。
