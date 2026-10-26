# 验证记录

日期：2026-10-27。验证按下列固定版本分别记录，源码、安装包和实际安装记录各自保存身份。

| 用户流程 | 已执行结果 | 对应记录 |
|---|---|---|
| 固定版本安装 | 独立版本目录、Codex 与官方 DSH 工具配置、macOS 后台启动 | 安装器返回实际程序位置、来源与服务心跳 |
| 官方 DSH 接续 | 两次独立模型会话完成数据导入、原生 Python、分析、图表、接续与导出 | 16 个项目文件的成果包 |
| 后台 Codex 继续研究 | 导入 DSH 成果，保留旧文件，追加分布分析和图表，再导出 | 约 483 秒，41 个项目文件，摘要逐项核对 |
| 官方 DSH 可视结果 | 浏览器中输入接续需求，实际调用 doctor、task_get、present | 文件卡、侧栏原图和 macOS 预览均已打开 |
| 安装包独立使用 | 全新环境安装 wheel，经 MCP 完成导入、分析、图片预览、导出、重新导入 | uv 与 pip 两条安装路径均完成 |
| 升级回退 | 连续回退至保留版本再恢复，研究数据保持 | 61 个项目文件摘要不变 |
| 已有项目备份恢复 | 三任务备份恢复到新目录，两份成果包摘要一致 | 原服务运行，恢复副本等待接管，成果链接指向新目录 |
| 连续迁移 | 二次备份恢复仍能找到第一次导出的包 | 保留历史位置并解析当前入口 |
| 上传与控制 | 上传等待期间完成独立分析，停止后未派发 | 队列、阶段证据与状态检查 |
| 执行规则变化 | 上传期间撤回授权阻止派发；修改规则后重新准备 | 旧、新规则与请求事件保留 |
| 自动检查 | macOS、Linux 执行软件检查及真实 wheel 安装流程 | GitHub Actions 按提交保存结果 |

DSH 实测运行时为官方 DeepSeek Harness 0.1.7-rc.2，沿用用户既有模型连接。任务接续与文件卡验证使用提交 `6fc1e63b21dedf4d1ceba78d27075b2b27f0ccc7` 的安装版本；后续上传与规则检查随下一个候选版本保留结果。

上述现有数据流程使用本地服务的实际响应记录，保留 50 个观测值、分析方法、原生脚本、图表和成果包。材料研究的 Ni 输入已经准备，计算尚未提交。

首个公开包 `v0.1.0-alpha.1` 固定在提交 `ebc7e9b332ce61299c9f62a2fe6ea93be1dfc0c2`。该安装包完成 64 项软件检查、独立 MCP 安装流程、官方 DSH 三个实际工具调用及后台 Codex 现有数据全流程。后台用时 273 秒，13 个项目文件与成果包摘要逐项核对，50 个观测值的中位数和 p95 另行重算一致。DSH 返回内容完整。包、安装器和手册摘要见发布附件 release.json 与 SHA256SUMS。

`v0.1.0-alpha.2` 固定在提交 `e9747cbeb9da9b9593586b367981f7d0737b46e1`，完成 67 项软件检查及固定包流程。官方 DSH 会话与持久服务的版本身份一致；后台 Codex 在 143 秒内读取真实连接回执、形成报告、登记成果并完成任务。旧版本证据与安装包保留。

后续提交 `13dcf349a63d882ce2d992fc3ddc300f8bc3e585` 完成 74 项软件检查和五百文件的安装包流程。官方 DSH 读取完整 skill、查询真实快照、按页检查清单、重新导出并核对完成要求；17 次工具返回均完整，原任务的三份交接包均经独立摘要检查。

`v0.1.0-alpha.3` 固定在提交 `7306577d234389acd6d1921f3077e8b6f35bce09`，完成 81 项软件检查及固定包完整流程。官方 DSH 在五百文件项目中调用 24 次工具，skill 和 MCP 返回均完整，五份历史交接包摘要逐项一致。后台 Codex 用时约 164 秒，生成的连接报告另行核对；期间实际安装返回 `service_busy`，十个配置文件与原后台进程保持不变，后台完成后再次安装成功。公开附件重新下载后核对安装包与安装器摘要。安装版本、完整响应读取和原历史共同纳入验收。

## 完整研究交付进度

| 验证项 | 当前进度 | 下一步 |
|---|---|---|
| 首项 Ni 研究 | 资源、脚本、工况与输出清单已准备 | 收到开始指令后运行完整流程 |
| 独立使用者 | 安装提示词与公开操作说明已编写 | 从安装入口完成新研究 |
| 24 项研究与 12 项保留任务 | 指标与分组已固定 | Ni 完整流程通过后逐类执行 |
| 36 次故障恢复 | 软件故障路径持续验证 | 按预定结果记录各类三次执行 |
| 八类科学反例 | 分类与判定目标已固定 | 使用原失败样本逐项核验 |
| 七天后台监视 | 心跳和启动记录持续保存 | 累积七天并核对退出、重启和断线 |
| 与直接 Agent 比较 | 两条比较路径和分母定义已固定 | 同模型、同资源执行配对研究 |

完整研究是否交付，按 ACCEPTANCE.md 中的原始要求、科学检查、可打开成果与接续能力共同判断。

## 2026-10-27 — Native input reuse and preserved storage

Fixed installed candidate `e819d7cc894d3944913dd88f34a2e6ae8535f340`, package version 0.1.0a6, wheel SHA-256 `106974153d89a349d9213c9925c1af2bad05ff9ce05fbe3ea74768808343fc8c`. 110 software checks and four macOS/Linux CI jobs passed. The isolated installed MCP journey exercised binding a retained prior input, failure recovery, analysis, preview, optimization, complete history export and reimport: 514 files and 37 operations. Official DSH called doctor, task_get and task_records; the loaded conversation identity was current and all three results were complete.

The installed storage operation processed a real existing research project. Independent before/after verification matched all 627 paths, byte hashes, sizes, permission modes and modification times. Thirty large files shared existing snapshot blocks; 27 were preserved in place. Observed free space increased from 3,668,824,064 to 9,035,972,608 bytes. Original archives and external source files remained intact. The next independent scientific review was registered through MCP and the same DSH task resumed from its retained deliverables.

## 2026-10-27 — Streaming export and fixed installed identity

Candidate `14738b83868cbf4b592a39aa9be0d66519338ef2`, package version 0.1.0a7, wheel SHA-256 `ce9dca567adae73eaad9bdaec7e1086131b9047784fc16bf320000cc9b1b0317`, passed 112 software checks and all four macOS/Linux CI jobs in PR #27. The installed MCP journey retained 514 project files and 37 operations through analysis failure, native input binding, repair, image preview, storage optimization, export progress and reimport. Official DSH verified the current installed identity with three complete tool responses.

During actual background scientific work, a repeated installation returned `service_busy`. Eleven configuration files and the service process remained unchanged. The background retained corrected plots, numerical checks and a four-page report when its 600-second turn expired. The task was handed over through pause/resume and its existing pending diagnosis; no new physical evaluation was created.

Independent verification of the existing scientific data compared 144,000 selected atomic rows and 50 native averaged rows against the original files. A second archive retained 954 project files; every stored size and hash matched its manifest. Subsequent scientific review reopened the report, corrected six findings and retained the previous archive. The final narrative statistic check and new handoff remain in progress in Issue #14.

The recovered background turn completed on the same a7 runtime. The four-page PDF and corrected sample statistic were independently read, all previously verified numerical files retained their hashes, and the third archive matched all 1,177 manifest entries (4,780,252,942 bytes). Repeating installation after the work completed succeeded with the same service process. The package is published as alpha.7. A README count mismatch and a rejected continuation intent remain recorded in Issues #14, #28 and #29.
