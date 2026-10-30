# 给 Agent 的安装指南

目标是让用户在当前对话里完成研究。识别可用能力，推进当前能完成的步骤，持续保存进度。

## 环境选择

| 当前能力 | 操作 | 回执 |
|---|---|---|
| 已有 MCP | 调用 doctor，核对版本与连接 | 实际工具回执 |
| 有本机命令 | 检查已有安装，使用固定版本安装器 | 真实程序路径 |
| 只有 MCP 且无已部署连接 | 引导进入能访问用户电脑的工作对话 | 保存进度与接续句 |
| 支持本项目插件包 | 安装对应版本插件并执行工具检查 | 插件、skill 与服务版本 |

v1.0 的托管安装与桌面后台使用 macOS。Linux 支持源码及 `--no-launchd` 隔离安装，持久服务需自行配置；Windows 原生安装尚未提供。先识别系统并据此说明可用路径。

当前仓库提供 Python 安装器、Codex 注册入口、DSH stdio 连接配置和 skill；插件分发包在交付记录中单列。

检查 `alm --version`、真实路径与 `alm doctor`。找不到命令时，使用安装回执中的 binary 完整路径。权限问题通过平台正常入口处理。同名连接指向其他安装时，核对原环境和数据目录再选择使用版本。

## 安装固定版本

### 先选择同一套发布文件

默认安装[正式版 v1.0](https://github.com/XPYY233/auto-lammps-mcp/releases/tag/v1.0)，Python 包版本为 `1.0.0`。记录标签、源码提交和安装包摘要。后续更新从 Releases 选择正式发布，显式指定候选版本时保留其验证记录。

从该版本的发布附件取得 `install.py`、wheel、`SHA256SUMS` 和 `release.json`；核对安装器与 wheel 的 SHA-256。读取相同标签下的本指南和 skill，例如发布页的源码入口指向 `blob/<tag>/docs/FOR_AGENTS_INSTALL.md`。保存文件实际位置后继续安装。

若用户需要当前源码中的新增能力，先展示与发布包的具体区别，选择固定 Git 提交并记录验证状态。已有可用安装先检查，保留原任务和配置。

### 使用发布 wheel

使用下载好的安装器及 wheel；下列路径与摘要由实际发布附件替换，host 选择当前平台：

```sh
python3 /absolute/path/install.py \
  --wheel /absolute/path/auto_lammps_mcp-VERSION-py3-none-any.whl \
  --sha256 VERIFIED_WHEEL_SHA256 --host codex
```

DSH 使用 `--host dsh`，并添加下文的 `--dsh-runtime` 与 `--dsh-profile`。安装器检查 wheel 摘要后建立独立版本环境。

### 使用固定源码

从仓库克隆并检出所选发布标签或完整提交，在这个干净的 Git 检出目录运行：

```sh
python3 scripts/install.py --source . --host codex
```

源码安装通过 Git 保存确切提交身份；已有用户修改的目录继续保留，另建干净检出用于安装。

安装器在独立版本目录建立环境，保存 installation-progress.json。重复执行复用已完成环境，包与连接成功后再切换 current。后续命令使用返回的绝对 binary 路径。

安装包包含固定依赖清单，普通 Python 与 uv 路径均安装分析环境。优先使用 uv 创建已验证的 Python 3.13 环境；已有 Python 需为 3.12 或更新版本。安装器的标准输出为 JSON 回执，安装日志写入错误输出。需要回退时使用同一安装器的 `--rollback`，恢复上一软件版本并核对原任务。

## 平台内接入

Codex：安装器通过现有 `codex mcp add` 注册 auto-lammps，并安装 skill。当前会话清单未刷新时，新建工作对话并粘贴：“继续配置 Auto-LAMMPS，从上次已经完成的步骤接着做。”实际调用 MCP doctor 与 task_list，确认本次 doctor 的 current_connection.state 为 current；host_calls 保留历史调用记录。

DSH 使用官方 DeepSeek Harness 的现有安装。运行 `python3 scripts/install.py --source . --host dsh --dsh-runtime /absolute/path/to/deepseek-harness --dsh-profile web`；runtime 也可以是已安装 dsh 命令的绝对路径。安装器检查实际版本，在所选 profile 中添加独立的 MCP 配置块，保留原模型设置，备份原文件并检查组合结果。配置失败会恢复原文件。已有 API 凭据继续由 DSH 管理。

安装器同时生成 dsh-mcp.json、dsh-harness-entry.json 和可供官方 `--patch` 参数使用的 dsh-auto-lammps.patch.yml。工具帮助通过 MCP 提供，skill 安装到 `.agents/skills/auto-lammps-mcp`。加载后在 DSH 中实际调用 doctor 与 task_list，回执中的 host_calls 记录调用时间与工具名称。

使用安装回执中的 runtime 启动官方 `--profile web`。它会打开带有现有模型配置的对话页面。选择工作区，在输入框粘贴接续提示词即可使用。默认端口被占用时，先识别已有服务；需要独立验证时，选用空闲端口，例如 `--port 3081`。保留启动时的登录链接在本机，接入后浏览器显示普通本地地址。

stdio 进程只桥接持久服务。平台探测、重载和多个聊天共享同一服务，退出平台后 macOS 后台继续工作。

升级后检查当前 MCP doctor 返回的 current_connection：current 表示本对话工具与后台版本一致；reload_required 表示本对话仍保留旧工具，需要重新加载连接；unverified_client 表示旧连接没有提供版本记录。重新加载后再次调用 doctor，保留原任务继续工作。host_calls 展示各平台最近一次调用；当前对话的结果以 current_connection 为准。

background.verification_state 显示后台接续是否由当前安装版本完成。previous_candidate 保留旧版成功记录；通过一次当前版本的接续后更新为 current。完整证据从回执指向的 operation_get 读取。

## 基础配置

先查已有 SSH 连接、项目和验证环境。集中询问仍缺的信息：研究目录、连接、账号、队列、远端目录、环境初始化、自动接续与通知。

cluster 配置需要 ssh_alias、remote_root、account、partition、environment_script。用实际已有软件的环境初始化命令，随后通过 connection_check 检查连接、Slurm 和目录。物理运行还要完成计算节点验证。凭据通过 SSH、系统或平台登录入口管理。

后台检查 Codex 实际路径与 `codex login status`。保存 background 的 enabled、codex、timeout_seconds；通过 background_request 验证能够读取任务并产生实际结果。notifications 的 enabled、desktop 控制桌面提醒。

## 完成回执与首用

分别展示安装包、当前对话工具调用、集群连接、计算节点环境、后台服务、后台 Agent、通知与下一步。配置完成后提供“新建研究、继续已有项目、分析已有结果”。

首次 Ni 计算展示工况、结构、势、输出、32 核/8 GiB/具体时限及复用资源。用户说“开始”后保存授权原话，再提交并持续跟进。最终交付温度、体积、总能、逐原子势能、轨迹与交接包。

安装中断从回执继续，已成功配置保留。重新打开会话时使用：“继续配置 Auto-LAMMPS，从上次已经完成的步骤接着做。”
