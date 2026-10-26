# 审核意见追踪

45 条意见逐项保留验收要求。实现与完整流程证据分别登记。

| 编号 | 要求 | 实现位置 | 验收状态 |
|---|---|---|---|
| R01-F1 | 把已登记链落实为可执行的阶段契约 | service.py, worker.py, slurm.py | 实现与完整流程验收进行中 |
| R01-F2 | 任务停止必须阻断并发派发 | service.py, worker.py, slurm.py | 实现与完整流程验收进行中 |
| R01-F3 | 幂等性不能由宿主自行换键绕过 | service.py, worker.py, slurm.py | 实现与完整流程验收进行中 |
| R01-F4 | 锁定 SDK 版本不足以保证运行中任务可升级 | service.py, worker.py, slurm.py | 实现与完整流程验收进行中 |
| R01-F5 | 最小交付按真实调用链切分核心 | service.py, worker.py, slurm.py | 实现与完整流程验收进行中 |
| R02-F1 | 口语指代缺少可核对的任务绑定 | installation.py, service.py, skill | 实现与完整流程验收进行中 |
| R02-F2 | 新手与熟练者缺少同一套澄清规则 | installation.py, service.py, skill | 实现与完整流程验收进行中 |
| R02-F3 | 长任务需要说明下一步由谁推动 | installation.py, service.py, skill | 实现与完整流程验收进行中 |
| R02-F4 | 可下载还不足以支持浏览和比较 | installation.py, service.py, skill | 实现与完整流程验收进行中 |
| R02-F5 | 错误解释必须对应不同恢复动作 | installation.py, service.py, skill | 实现与完整流程验收进行中 |
| R03-F1 | 科学选择必须成为我能审阅的级联方案 | science.py, native analysis | 实现与完整流程验收进行中 |
| R03-F2 | 参考晶格与新增缺陷不能只记作输入文件 | science.py, native analysis | 实现与完整流程验收进行中 |
| R03-F3 | 重复种子、续算和失败重试必须分开 | science.py, native analysis | 实现与完整流程验收进行中 |
| R03-F4 | 长任务要报物理时间并能定位轨迹 | science.py, native analysis | 实现与完整流程验收进行中 |
| R03-F5 | 均值不能代替逐原子证据来诊断异常 | science.py, native analysis | 实现与完整流程验收进行中 |
| R04-01 | 固定条件需要落实为可核对的界面几何 | science.py, native analysis | 实现与完整流程验收进行中 |
| R04-02 | 应力应变的单位正确仍可能比较了不同试验 | science.py, native analysis | 实现与完整流程验收进行中 |
| R04-03 | 任务版本不能替代工况矩阵与比较分组 | science.py, native analysis | 实现与完整流程验收进行中 |
| R04-04 | 应力突降需要科学诊断，不能直接变成修错 | science.py, native analysis | 实现与完整流程验收进行中 |
| R04-05 | 曲线与结构应按同一事件一起浏览 | science.py, native analysis | 实现与完整流程验收进行中 |
| R05-01 | MSD同名结果可能具有不同科学定义 | science.py, native analysis | 实现与完整流程验收进行中 |
| R05-02 | 拟合窗口必须能从曲线质疑与复核 | science.py, native analysis | 实现与完整流程验收进行中 |
| R05-03 | 误差条不能把相关采样当独立重复 | science.py, native analysis | 实现与完整流程验收进行中 |
| R05-04 | 采样不足必须导向明确的研究动作 | science.py, native analysis | 实现与完整流程验收进行中 |
| R05-05 | 温度组完成不等于输运研究收敛 | science.py, native analysis | 实现与完整流程验收进行中 |
| R06-F1 | 原生脚本承诺缺少项目级导入与shell入口契约 | files.py, slurm.py | 实现与完整流程验收进行中 |
| R06-F2 | 完整自定义环境必须成为可导入的运行对象 | files.py, slurm.py | 实现与完整流程验收进行中 |
| R06-F3 | 模型与数据版本要进入分支依赖 | files.py, slurm.py | 实现与完整流程验收进行中 |
| R06-F4 | 数值异常需要保留科学失败路径 | files.py, slurm.py | 实现与完整流程验收进行中 |
| R06-F5 | 净收益对照要覆盖熟练用户已有资产 | files.py, slurm.py | 实现与完整流程验收进行中 |
| R07-F1 | 从需求覆盖补到结论证据，不能把同名指标当同一科学量 | science.py, acceptance | 实现与完整流程验收进行中 |
| R07-F2 | 势的可调用性与当前问题的适用证据必须分开 | science.py, acceptance | 实现与完整流程验收进行中 |
| R07-F3 | 自动修订应保留探索路径，避免把调到满意当科学成功 | science.py, acceptance | 实现与完整流程验收进行中 |
| R07-F4 | 交接科研判断，才能形成课题组能力 | science.py, acceptance | 实现与完整流程验收进行中 |
| R07-F5 | 验证集按科学推理类型留出，避免在历史故障上自证 | science.py, acceptance | 实现与完整流程验收进行中 |
| R08-F1 | 任务恢复缺少必须重建的决策状态 | service.py, acceptance | 实现与完整流程验收进行中 |
| R08-F2 | 短反馈缺少完整性与可读取性保证 | service.py, acceptance | 实现与完整流程验收进行中 |
| R08-F3 | 目标与检查同源，可能形成自我证明 | service.py, acceptance | 实现与完整流程验收进行中 |
| R08-F4 | 有预算约束仍缺少无进展停止与归因 | service.py, acceptance | 实现与完整流程验收进行中 |
| R08-F5 | 回归样本与经验回流会污染增益判断 | service.py, acceptance | 实现与完整流程验收进行中 |
| R09-01 | 复用应切在提交服务，不能把候选入口直接改名 | core.py, worker.py, installation.py | 实现与完整流程验收进行中 |
| R09-02 | 只读导入必须绕开会写数据库的构造器 | core.py, worker.py, installation.py | 实现与完整流程验收进行中 |
| R09-03 | 现有跟进器不是可恢复的分析任务队列 | core.py, worker.py, installation.py | 实现与完整流程验收进行中 |
| R09-04 | 依赖升级不能持续挤占科研时间 | core.py, worker.py, installation.py | 实现与完整流程验收进行中 |
| R09-05 | 首个完成条件应约束维护成本，而非工具数量 | core.py, worker.py, installation.py | 实现与完整流程验收进行中 |
