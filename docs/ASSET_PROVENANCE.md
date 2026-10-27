# 资产来源与复用

| 资产 | 来源 | 使用方式 | 验证 |
|---|---|---|---|
| 原提交与记账设计 | auto-lammps-run，源码版本 7dfead36158752235a41fd2d680e962a4c570d8e，Apache-2.0 | 审阅账本、执行入口与分析流程，保留执行意图、核对和历史规则；新核心独立实现 | 重复提交与未知回执测试 |
| MCP Python SDK | modelcontextprotocol/python-sdk，MIT | 标准 stdio 协议与工具 schema | 实际 MCP 客户端集成检查 |
| Pint | hgrecco/pint，BSD-3-Clause | 单位解析与量纲换算 | 四种立方埃表达和不兼容量纲 |
| NumPy / Matplotlib | 各项目公开发行包，BSD / Matplotlib license | 原生科学统计与绘图环境 | 独立安装与分析检查 |
| 九视角 45 条意见 | 用户组织的四轮审核 | 逐项写入 review-traceability.json | 对应用户验收要求 |
| 历史研究结果与资源 | 原项目私有研究记录 | 只读检索和导入新结果项目 | 摘要与来源核对、原记录保留 |

旧科研数据、作者源码、论文附件、执行日志和答案保持在研究目录。每个新复制代码文件单列原始路径、版本、许可、修改与验证。依赖版本记录于 requirements.lock.txt。

旧版 candidate_tools.py（workflow adapter v6）和 scientific_adapters.py（stage adapter v6），Apache-2.0，保留其“定义 compute 不会调用它、阶段变化后重新计算、官方版本匹配”的经验。新版独立实现原生命令文档读取与检查，没有复制旧编译器或作者程序。官方文档按指定发行标记从 lammps/lammps 的 doc/src 获取并在用户本地缓存；保存来源、版本和摘要，不把上游文档复制到本项目发行包。验证包含三次 Ni 原失败模式、原生 dump 与 reduce/thermo 用法及输入快照一致性。
