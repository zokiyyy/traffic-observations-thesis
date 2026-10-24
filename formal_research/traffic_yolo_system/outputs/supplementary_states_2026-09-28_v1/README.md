# 补充五路口来源内状态分析 v1

主入口：[补充五路口交通状态与证据不足研究报告](补充五路口交通状态与证据不足研究报告.md)。各路口子目录有逐窗JSON/CSV、阈值与校准索引、提示事件、例子和中文说明；`figures/`有10张英文图及对应PDF。

本轮只复用已完成的补充来源10秒计数和运动证据，没有重新运行YOLO、ByteTrack或全量逐帧聚合，也没有修改原来源、Unity或Main Test结果。NE12th是Train、408.576856秒。全部阈值是补充来源各自离线校准，未做固定原基准迁移。

## 阅读字段

| 字段 | 含义 |
|---|---|
| `relative_count_level` | 冻结规则的本源数量等级；empty仅为零观察，不能当作真实道路空闲 |
| `motion_median_px_s` | 已有可用绝对图像运动；缺失null，不是道路速度 |
| `motion_low_relative` | 仅在当前运动和本区P25均可用时为布尔值，否则null |
| `state_hint` | 冻结分类器原提示，完整保留用于追溯，可能在运动不足时仍有高数量提示 |
| `evidence_state` | 建议未来UI使用的综合显示；任何时间、数量校准或运动证据不足均unknown |
| `evidence_unknown_reasons` | 多个原因可共存，原因时长不能直接相加 |
| `high_count_persistent_count_only` | 纯数量条件持续≥30秒的布尔值，联合提示是其子集，不能相加 |
| `joint_hint` | 高数量与相对低运动同时持续≥30秒 |
| `confirmation_source_time_s` | 原来源窗口结束秒数；阈值来自全段离线校准，不是实时可用性声明 |

P33/P67相等时不能强行三档；运动P25至少需要30个合格运动窗。当前有绝对运动而P25不足时保留数值，综合显示unknown。长保持单帧>0.5秒导致其相交整个10秒窗排除校准、计时清零、运动null。数量保留仅用于追溯。

## 复现与证据链

`input_manifest.json`记录本阶段直接读取的小型文件SHA；`states_10s.json`的lineage记录上游原逐帧日志和原窗口SHA。原全帧/视频的字节校验沿用配对分析已经完成的证据链，本阶段不重复扫描。

冻结算法在`code/frozen_engine.py`，字节与首批最终校验完全相同。`code/derive_states.py`只调用calibrate和classify_windows；保守综合显示是单独的add_display函数。`verification/`包含独立逐行验证和30项边界检查，`research_notes/`包含第二份研究解释审查。

当前运行环境：`/Users/zoki/anaconda3/envs/traffic-yolo/bin/python`。数值派生/独立核验主要使用标准库；图表使用现有Matplotlib和NumPy。未新增依赖。

为保护v1，完整复现应在同一outputs下创建新的兄弟版本目录，复制`code/`、`verification/`、`research_notes/`和`figures/`内的Python源码（保留结构）、`code/frozen_engine.py`、本README及各子目录说明文档，随后执行：

1. `code/derive_states.py`（拒绝覆盖已有derivation_completion.json）
2. `verification/test_frozen_boundaries.py`、`verification/test_display_boundaries.py`及`verification/verify_derived.py`
3. `research_notes/probe_calibration.py`和`research_notes/review_derived_outputs.py`
4. `figures/plot_states.py`，重新目视审图并保存visual_qa.json审阅凭据
5. `code/build_report.py`、`verification/review_report.py`
6. `code/final_checks.py`

脚本从自己的版本目录定位输出，从父outputs定位既有来源。重新生成报告前保留verification/README.md和research_notes研究审查；目视图表审阅需要重新执行，不能自动沿用本版人工目视完成标记。生成时间及PDF元数据可能变化，数值阈值、状态、事件应一致。

本版末次状态数值与报告核验见[final_verification.json](final_verification.json)。提示数量及未知比例都是模型观察与规则行为的描述，不是交通状态准确率，也不能给路口拥堵排名。
