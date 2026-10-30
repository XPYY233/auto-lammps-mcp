# 项目开发与交付

新增能力见[本地记忆与验证复用](MEMORY_REUSE.md)。`research_store.py` 定义版本化记忆与 schema 2，`research.py` 提供公共接口，`validation.py` 比较快照和实测组件，`verification.py` 执行固定检查，`checkers.py` 关联原生检查程序，`node_guard.py` 在计算节点增量监视。Service、CLI、MCP 共用这些接口；Worker 在上传前和派发前再次检查。

新接口参数从 `help(method)` 生成。新增数据由事务和 op_id 管理；批次成员拥有独立快照、样本、种子和作业，复用原验证回执。变更只允许声明的种子归一化；所有其他差异明确返回。回执失效沿引用关系传播。

`runtime_probe.py` 读取实际程序、外部依赖及共享库身份，同一实现用于派发前与计算节点核对；`runtime_evidence.py` 保存原观察记录和最新观察索引。`environment_capture` 返回持久操作，`validation_plan(runtime_id=...)` 固定身份。节点的 `.alm-runtime.json` 通过回收摘要进入组件检查，文件改变或缺失会使关联回执失效。运行时身份缺失的旧方案保留历史，补齐对应检查后复用。

已有 Agent 直接写科研脚本并根据反馈修正；服务承担持久执行、监视、恢复、分析和交接。

| 模块 | 职责 |
|---|---|
| core.py | 事务、任务版本、单执行者操作权、事件、操作回执 |
| service.py | 任务、文件、资源、执行、分析和成果公共接口 |
| files.py | 项目导入、局部修改、摘要、快照与来源失效传播 |
| slurm.py | SSH、上传、一次提交、核对、核算与回收 |
| remote_read.py | 有界日志读取、实际输出时间与热力学进展 |
| worker.py | 持久阶段链、分析队列、监视线程、Codex 接续与通知 |
| transport.py | 私有本机连接、单一后台进程、协议核对 |
| mcp_server.py / cli.py | 同一服务的 MCP 与命令行入口 |
| installation.py | 自检、连接、skill 与 macOS 后台安装 |
| dsh.py / network.py | 官方 Harness 接入、配置保留与已有系统代理发现 |
| handoff.py / backup.py | 带来源记录的成果包导入、完整备份与独立目录恢复 |
| science.py | 单位、热力学列、逐原子轨迹检查 |
| delivery.py | 批次分析清单、成果版本目录、交接说明 |

原生项目入口接受快照和 shell 脚本。工况、独立样本、重试、续算和分析各有身份。运行绑定环境与输入；更新环境产生新指纹。

`alm help` 和 MCP help 从代码生成工具说明和参数。任务变更检查版本，可重试操作携带 op_id；同编号同输入返回原回执，不同输入返回冲突。耗时操作返回 operation_id，随后读取结果。文件分页返回范围、摘要、截断与下一个偏移。错误提供原因、已发生影响和恢复动作。

## 开发流程

每项工作从用户成果出发，记录当前断点与安装版本验证方式。Issue 关联审核及复盘要求，独立分支提交 PR，自动检查和评审形成候选版本。修复回到原失败输入与完整流程。

新分析方法由原生 Python 实现，框架维护入口、输入、输出和依赖。软件检查、集群运行、独立用户流程、科学验收和七天监视分别记录。里程碑汇报：“用户新增完成了什么｜还有什么未交付｜证据在哪里｜下一步是什么”。

自动检查先测试源码，再建立独立的 wheel 安装环境，使用 `scripts/verify_installed.py` 完成 MCP 创建任务、导入、原生分析、图片预览、成果包要求检查、导出和重新导入。安装包携带同一依赖锁文件、skill 与使用指南。

对话默认读取最近五条进展，完整记录按页读取。导出的 research-record.json 包含全部操作的原始参数、结果和状态；events.json 保存同一时点的完整事件。安装包验证加入超过三十条操作的项目，核对导出与重新导入后的历史完整性。

大项目的 operation_get 优先返回操作状态与快照编号，超出对话长度的明细通过 operation_read 读取。path 是逐层字段名称列表，文件名保留原样；对象与数组分页返回条目，长文本按字符偏移读取。每页带 next_offset 和 more，导出保留全部原始内容。固定安装包检查覆盖五百个原生输入文件和完整清单读取。

MCP 返回处统一检查内容长度，包括显式请求完整明细的情况。过长响应按内容摘要持久保存，返回状态、编号与 response_read 入口；换对话或重启后仍能逐页读取全部字段和文本。完整错误日志保留相同入口。本地命令行继续支持完整读取，备份同时保存已发布的响应记录。

skill 入口保留任务选择、执行规则与结果读取方法，安装、研究、恢复和交付细节按需读取。安装包同时携带这些说明，版本指纹覆盖整个 skill 目录，保证关联说明变化也进入候选核对。

## 资产复用

旧账本、Slurm 查询、传输和分析进程代码已经审阅。旧候选入口按原生文件重新组织，保留一次执行意图、未知提交先核对和独立核算规则。当前核心是新模块；依赖由锁文件固定。复制旧代码前逐文件登记来源版本、许可、修改和验证。

NbTiZrMoV、CoCrNi、FeAl、W 与已完成 MD 数据以结果项目接入，保留原数据和费用。论文身份与条件沿用文献工作台，资源优先查现有库。旧数据库通过只读连接访问，新服务使用独立目录与新身份。

## 交付物

固定源码与安装包、安装提示词和安装器、Codex/DSH 连接、skill、任务服务、后台接续、执行/分析/可视化/导出能力，以及用户、开发、恢复、迁移和验收文档。手册交付 PDF 与可编辑 TeX。

## 复用已有分析数据

`batch_dataset` 根据实际回收文件生成样本清单，保留工况、种子、重试链及排除原因。`analysis_submit(batch_id,batch_files)` 固定文件摘要，工作进程再次核对并生成 `batch-samples.json`；原生脚本选择统计方法。`method` 记录定义、区间及不确定度，`supersedes` 关联历史分析；完成和失败自动进入本地记忆，保留观察状态。

`results_get` 读取需求覆盖、当前/失效/被替代的成果、近期分析与导入来源。`artifact_register` 的 `description` 和 `supersedes` 字段将新路径成果与旧编号关联，保留原文件及全部要求；`completion_check` 检查当前有效结果。元数据放入 schema 2 的现有 settings，随备份和研究记录导出。交接包 README 列出相对路径；重新导入保留原始成果目录与操作记录。

`trajectory_correlate` 对齐轨迹、热力学实际采样及执行阶段，保留重复步数歧义。解析器读取 `ITEM: TIME`、单位、盒原点/边向量和按原子编号的类型变化。类型固定检查按任务选择，时间缺失时返回缺项。定义来自官方 [dump_modify](https://docs.lammps.org/dump_modify.html) 和 [thermo_style](https://docs.lammps.org/thermo_style.html)。

`analysis_submit` 的 `input_bindings` 将工作目录内路径映射到项目原文件。例如脚本读取 `data/input.csv`，可绑定到先前分析的 `analysis/<operation>/data/input.csv`。回执保存映射、原文件摘要和副本方式。输入文件保持独立，输出、失败与重试继续保留。

`project_optimize` 通过持久队列整理已存在快照的大文件。支持文件克隆的卷共享未修改的数据块，逐个核对摘要、模式、属性和修改时间后原位替换；扩展访问规则的文件留在原位。任务暂停或操作权变化时保留已完成路径和部分回执。新工具与原生文件、分析、交接使用同一个任务服务。

## 审核意见与用户验收

[45 条审核意见](REVIEW_TRACEABILITY.md) 逐项列出已有结果、证据和下一项用户验收。`review-traceability.json` 保留原始要求和验收条件；更新后运行 `python scripts/render_review_traceability.py` 生成可读记录。自动检查核对编号、文件引用与两种记录的一致性。软件检查、安装验证和完整研究分别保留实际结果。
# Task-specific validation and recovery

`phases.py` reads executed commands beside thermo output. Local verification and the standalone node monitor share it. Phase selectors use actual fixes/groups/time steps or native markers. The parser returns scoped data errors and phase facts; it does not assign a scientific tolerance. `log_phases` exposes that evidence to the host Agent.

Checks carry required/advisory/candidate roles; monitors additionally choose warn/stop. Candidate comparisons preserve all measurements without invalidating unrelated required evidence. `validation_compare` reads alternatives; `validation_plan(reuse_sources=..., rationale=...)` merges compatible measured components. Native qualified Python checkers attach through component `checker_id` and `validation_check(analysis_operation_id=...)`. Old collected runs can be rechecked when their actual conditions and input dependencies match. Missing/failed measurements invalidate their own quantity and component.

Regression coverage includes cooling-phase mismatch, omitted first NVE, phase continuity across time-step changes, partial logs, candidate failures, scoped reuse, explicitly requested stopping, and qualified custom checks on an existing accounted run. Frozen criteria and history remain available after revision.

## 批次阶段实现

`batch_views.py` 从持久成员与运行回执生成统一汇总，供 task_get、batch_list、batch_get 及 Worker 的阶段事件和五分钟摘要复用。详情包含输出字段、证据时间与未知费用，不复制长日志。`batch_observation:<id>` 保存上一次状态以避免重启后的重复完成事件；控制状态与计算结果分别保留。提交异常与最新 evidence 合并，保留提交期间写入的取消请求。

`tests/test_batch_progress.py` 使用实际服务和队列完成20成员调度、控制交接、丢失回执、独立批次失效范围及真实 MCP/服务重启。阶段检查在隔离目录运行；完整科研批次、双平台与长期监视按最终验收清单统一执行。
