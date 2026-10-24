# 五路口配对来源观察分析 v1

先读[中文总报告](五路口原案例与补充时段独立对比总报告.md)，meeting可先读[快速结论](meeting快速结论.md)。五个详细案例在 `cases/`；英文图表在 `figures/`，同时提供PNG和PDF；原图/既有预览追溯在 `evidence/`。

本目录只做已完成来源的描述性比较，没有检测/跟踪重跑、相对状态校准、固定阈值迁移或准确率评估。NE12th补充始终为Train、408.577秒。原输出和Main Test结果保持独立。

## 计算及检查

- `code/analyze_counts.py`：校验计数CSV与原QC哈希、逐帧原整数PTS/时长，从逐帧计数重新积分10秒窗口；计算加权经验分布、保持敏感性与源内四分段趋势，绘制数量图。默认拒绝覆盖已有count_analysis.json。
- `motion/code/derive_motion.py`：原运动结果按哈希复用；补充运动从完整记录重算。`engine.py`是既有算法快照；只调用运动聚合，不调用校准或状态分类。`motion/code/verify_motion.py`独立检查结果。
- `audit/audit_comparability.py`：来源字节SHA、冻结划分、ROI、数值参数、运行库和处理代码核对。
- `evidence/build_evidence.py`：复用既有带框样例和抽取少数原图，保留准确原PTS、帧号及哈希，不改变检测结果。
- `code/plot_motion_and_examples.py`：英文运动证据图和Eastgate会议趋势图。
- `code/build_report.py`：从结构化结果生成中文报告。
- `code/verify_delivery.py`：各模块窗口、数量、缺失与保持交叉检查，加权分布/分段守恒检查，输入哈希冲突检查和交付文件哈希清单。

原时间基为1/90000秒。加权数量分布使用逐帧显示时间，不把高帧率当作大量独立样本。长保持定义为单帧>0.5秒；计数保留原显示区间，另给剔除该帧区间的敏感性均值；运动则将相交的整个10秒窗设未知。空值不填0。

## 复现

本次数量、图表及总报告使用已有环境 `/Users/zoki/anaconda3/envs/traffic-yolo/bin/python`，Python3、NumPy2.2.6、Matplotlib3.10.9；运动/审计主要使用标准库，图像拼接使用Pillow和已有本地FFmpeg。未安装新依赖。推理运行库版本见audit报告，不要与本机分析环境混为一谈。

只复算数量时，从工作区运行以下命令，`--output`必须是新的目录：

```sh
/Users/zoki/anaconda3/envs/traffic-yolo/bin/python \
  formal_research/traffic_yolo_system/outputs/paired_case_comparison_2026-09-28_v1/code/analyze_counts.py \
  --output /private/tmp/paired_count_recheck_v1
```

完整重现建议在原outputs下创建新版本兄弟目录，复制上述脚本、`motion/code/engine.py`和本README，保留相同子目录结构，再按以下顺序执行，避免覆盖已审阅v1：

1. `audit/audit_comparability.py`
2. `code/analyze_counts.py`
3. `motion/code/derive_motion.py`，以及该目录中的独立核验脚本
4. `evidence/build_evidence.py`
5. `code/plot_motion_and_examples.py`
6. `code/build_report.py`
7. `code/verify_delivery.py`

脚本以所在新版本目录定位输出，以其父outputs目录定位固定旧/补充来源。完整视频仅用于SHA和少数静态抽帧，不导出全长带框视频。视觉目视结论和论文解释需要结合输出重新审阅；生成时间、哈希清单的路径以及PDF元数据可能变化，数值应一致。v1的原数字、输入身份和检查结果由 `count_verification.json`、`motion/verification.json`、`audit/audit_checks.json`及 `final_verification.json`保存。

## 关键边界

P33/P67及原相对低运动P25来自每段自身离线校准，因此本任务不用High/Mid/Low比较来源。运动描述性分位数也不是新的相对阈值。没有将ID总数当车流；没有真实密度、道路速度、拥堵或计数准确率结论。来源内光照会改变可检出性，日夜/不同日期/短Train不能当作受控时段实验。
