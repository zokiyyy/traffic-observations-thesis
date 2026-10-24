# 五路口两批来源：绝对图像运动证据

快速结论：本模块可以检查同一选定区域中可用轨迹的图像位移，以及有多少时间无法形成运动证据。它不产生跨来源 High/Mid/Low 比较，不重校准相对运动阈值，不执行固定基准迁移，也不产生新增交通状态。

- NE8th 补充夜间的 road_left、road_top、road_right、junction_center 运动未知时间分别约 95.28%、90.56%、98.06%、100%。少量被选中的有效窗口无法代表整段道路运动。
- NE12th 原夜间五区运动未知时间为 93.89%–100%；补充白天短源为 21.68%–82.87%，但后者仅 408.577 秒且属于 **Train**。不能将两者当作等长独立验证，不能把运动证据差异解释成真实速度差异。
- Eastgate 补充 road_left 没有满足门槛的运动窗口；未知不是静止，也不是畅通。
- 其余区域的像素运动分布可以作为模型观察的描述，但必须同时展示可用时长，并先由主报告核对模型、后处理、ROI 和光照可比性。有效窗口分布具有证据选择偏差。

## 算法与口径

本任务复制并逐字节校验既有 `five_intersection_states_2026-09-28_v1/code/engine.py`，只调用 `aggregate_frames` 与 `build_windows`。原五段直接复用已封存 `states_10s.json` 的绝对运动字段，并独立验证全部证据门槛；补充五段使用已完成的逐帧数据和原始 PTS 派生相同绝对运动字段。没有重新执行检测或跟踪。

对连续观察、同 ID、同区域的车辆，使用检测框底部中心在 1.0–1.2 秒原始 PTS 间隔上的位移除以时间，单位为源图像 `px/s`。在每个 10 秒窗口内，先对每个 ID 的运动值按有效时长求均值，再按各 ID 的运动有效时长取加权中位数。像素值受透视、位置、遮挡、检测框抖动和轨迹碎片影响，不能换算为 km/h，也不能跨区域排名。

运动值只在完整有效的 10 秒窗口、运动车秒覆盖率至少 50%、累计运动证据至少 5 车秒且不含长保持时输出。任一原始呈现间隔 >0.5 秒，所有被其覆盖的窗口均运动未知，且前后重置历史；缺失观察、换区、无效帧和帧序号不连续也重置历史。尾窗保留计数和证据量，但运动值未知。所有未知均为 JSON `null`，不填零。

本模块没有调用 `calibrate` 或 `classify_windows`，所以**不存在新增 P25 相对低运动阈值、P33/P67 计数标签或 30 秒持续状态**。已有相对规则仍仅属于各自来源的离线基准。

## 文件

- `motion_summary.csv` / `motion_summary.json`：44 个来源×区域汇总行。
- `Original/<case>/motion_10s.json` / `.csv`：原五段绝对运动窗口。
- `Supplementary/<case>/motion_10s.json` / `.csv`：补充五段绝对运动窗口。
- `method.json`：版本和精确定义。
- `input_manifest.json`：所有本模块读取的数据、回执、ROI 与代码的 SHA-256、大小和修改时间。
- `verification.json`：逐帧来源/划分/ROI/配置绑定、帧哈希、逐行 PTS、长保持、10 秒计数复算与输入不变性检查。
- `independent_verification.json`：不用 engine 的已保存输出校验，包括运动门槛、时长、CSV 行数、分位数、原运动字段数值完全复用。
- `code/derive_motion.py`、`code/engine.py`、`code/verify_motion.py`：复现与校验脚本。

`available_motion_time_fraction` 是有可用运动值的窗口时长除以整段时长；`unknown_motion_time_fraction` 为其补数，因此包含无观察、连续性/覆盖不足、少于 5 车秒、长保持与尾窗等情况。`available_among_complete_observed_windows` 仅以至少有观察的完整且无保持窗口作为分母，便于区别空观察窗口和已有观察但运动不足。`raw_motion_exposure_coverage` 是门槛判定之前的连续轨迹车秒除以当前观察车秒，**不等于可用运动时间比例**；被整窗排除的保持窗口仍可能含部分车秒，因此不可将该列当作最终可用证据。

`motion_p25_px_s`、`motion_p50_px_s`、`motion_p75_px_s` 是**可用 10 秒窗口运动中位数**的时间加权分布分位数，采用累计权重达到目标比例的最小值；所有可用窗均为 10 秒。它们不是整段所有车辆的速度分位数，也没有把未知时间补入样本。`held_duration_s` 是原始长呈现间隔总时长；`held_affected_window_duration_s` 是被它们排除的整个窗口总时长，两者不可混用。

## 复现

在任意工作目录运行以下命令（全部输出仅覆盖本模块目录）：

```sh
python3 -B /Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/motion/code/derive_motion.py
python3 -B /Users/zoki/Desktop/traffic_projects/formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/motion/code/verify_motion.py
```

原 Newport 已有状态文件将划分拼作 `validation`，本模块仅规范输出为 `Validation`，同时保留 `prior_split_spelling`；来源原哈希由既有生成回执取得。NE12th 补充保留 `Train`。没有读取 Main Test 图片/标签，没有修改原成果，没有处理新视频或改动 Unity。工程一致性检查不评估真实车速、真实密度或拥堵准确率。
