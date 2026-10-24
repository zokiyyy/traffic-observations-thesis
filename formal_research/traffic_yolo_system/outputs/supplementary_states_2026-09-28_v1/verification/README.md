# 独立核验与边界测试

**核验通过：5 个补充来源、22 个区域、6,342 个区域窗口、247 个提示事件；30 项边界测试全部通过。** 独立数据核验代码不导入生产派生函数或冻结引擎，自行重算分位数、状态、持续时间与综合证据显示。只读已封存的小型 10 秒数据和本轮输出，不遍历原帧日志或视频。

- [独立核验结果](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/independent_verification.json)：阈值、每窗数据、冻结状态、综合 unknown、提示事件、汇总、CSV 与示例全量检查。
- [冻结规则 19 项边界](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/boundary_tests.json)：插值、29/30 校准样本、等阈值、20/30 秒、缺失/保持/尾窗/不连续时间、运动缺失与 Train 身份。
- [实际显示层 11 项边界](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/display_boundary_tests.json)：纯数量提示与综合未知分开、绝对运动与校准不足分开、零观察不能畅通、首次确认时刻及联合子集。
- [研究报告数字复核](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/report_review.json)：总览、22 行区域表、示例时间范围、首次联合确认、保持区间及 unknown / state_hint 的解释。
- [中文审查结论](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/review_findings.md)与[方法审查](/Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/method_review.md)。

3,674 个区域窗缺少绝对运动证据并保留 null；90 个区域窗有绝对运动但运动 P25 校准不足，保留绝对值、相对低运动为空、综合状态 unknown。41 个区域窗满足纯数量持续条件但综合证据不足，数量提示为 true 且综合状态 unknown。联合提示是数量提示的子集，不能把两者直接相加。

NE12th 保留 Train、408.5768556 秒，只有 road_right 达到运动 P25 的最低校准样本数。NE8th 四区数量阈值重合，综合状态全部未知。结果用于来源内模型观察和规则可解释性，不是交通或拥堵准确率验证，也不是固定阈值迁移实验。

复现命令：

```sh
python3 -B /Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/test_frozen_boundaries.py
python3 -B /Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/test_display_boundaries.py
python3 -B /Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/verify_derived.py
python3 -B /Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/supplementary_states_2026-09-28_v1/verification/review_report.py
```

全部复现输出仅写本 verification 目录。数据核验结果记录所读文件 SHA-256；测试结果记录被测试引擎、派生脚本和核验脚本 SHA-256。工程校验不能替代独立交通真值。
