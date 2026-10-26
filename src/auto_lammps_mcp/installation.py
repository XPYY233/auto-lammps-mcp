"""Resumable local installation and host-specific connection receipts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import time

from . import __version__
from .core import Fault, encode, require
from .files import atomic_write


def codex_path():
    candidates = [shutil.which("codex"), "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"]
    return next((p for p in candidates if p and Path(p).is_file()), None)


def validate_config(section, values):
    require(section in ("cluster", "background", "notifications"), "配置项应为 cluster、background 或 notifications")
    require(not any(re.search(r"password|secret|api.?key|private.?key|token", key, re.I) for key in values), "请通过 SSH、系统或平台登录入口管理凭据")
    if section == "cluster":
        require(set(values) == {"ssh_alias", "remote_root", "account", "partition", "environment_script"}, "集群配置需要 ssh_alias、remote_root、account、partition、environment_script")
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", values["ssh_alias"]), "使用 SSH 配置中的连接名称")
        require(re.fullmatch(r"/[A-Za-z0-9_./-]+", values["remote_root"]) and ".." not in Path(values["remote_root"]).parts, "远端目录应为绝对路径")
        require(all(re.fullmatch(r"[A-Za-z0-9_.-]+", values[k]) for k in ("account", "partition")), "账号或队列名称无效")
        require(isinstance(values["environment_script"], str) and values["environment_script"].strip(), "请提供已核对的环境初始化命令")
    elif section == "background":
        require(set(values) <= {"enabled", "codex", "timeout_seconds", "profile"}, "后台配置字段无效")
        require(type(values.get("enabled")) is bool, "enabled 需要布尔值")
        require(60 <= values.get("timeout_seconds", 900) <= 3600, "后台单轮时限为 60–3600 秒")
        if values.get("codex"):
            require(Path(values["codex"]).is_file(), "Codex 程序路径不存在")
    else:
        require(set(values) <= {"desktop", "enabled"}, "通知配置字段无效")
        require(all(type(v) is bool for v in values.values()), "通知选项需要布尔值")


def doctor(store):
    aliases = []
    ssh_config = Path.home() / ".ssh/config"
    if ssh_config.is_file():
        for line in ssh_config.read_text().splitlines():
            match = re.match(r"\s*Host\s+(.+)", line, re.I)
            if match:
                aliases.extend(name for name in match[1].split() if not any(c in name for c in "*?!"))
    cluster = store.setting("cluster")
    background = store.setting("background", {})
    from .transport import socket_path
    receipt = dict(version=__version__, protocol=1, schema=1, python=sys.executable, package_path=str(Path(__file__).parent),
                   state_directory=str(store.home), platform=platform.system(),
                   daemon=dict(socket_exists=socket_path(store.home).exists(), managed=store.setting("launchd", False)),
                   hosts=store.setting("hosts", {}), host_calls=store.setting("host_calls", {}),
                   cluster_configured=bool(cluster), cluster_receipt=store.setting("cluster_receipt"),
                   environment_receipts=store.setting("environment_receipts", []),
                   background=dict(configured=background, executable=codex_path(), receipt=store.setting("background_receipt")),
                   available_ssh_aliases=sorted(set(aliases)))
    next_actions = []
    if not receipt["host_calls"]:
        next_actions.append("在当前 Agent 平台实际调用 doctor；配置文件存在还需要会话加载")
    if not cluster:
        next_actions.append("选择 SSH 连接、账号、队列、远端目录和已有环境")
    elif not receipt["cluster_receipt"]:
        next_actions.append("调用 connection_check，核验连接和目录")
    if not receipt["environment_receipts"]:
        next_actions.append("确认首次计算内容与资源，完成计算节点环境验证")
    if not receipt["background"]["receipt"]:
        next_actions.append("检查 Codex 登录并完成后台接续验证")
    receipt["next_actions"] = next_actions
    receipt["resume_prompt"] = "继续配置 Auto-LAMMPS，从上次已经完成的步骤接着做。"
    return receipt


def install(store, host="none", managed=True):
    require(host in ("codex", "dsh", "none"), "选择 codex、dsh 或 none")
    binary = str(Path(sys.executable).parent / "alm")
    require(Path(binary).is_file(), "命令行入口尚未安装", "missing_installation", "先安装当前版本 Python 包")
    connection = dict(command=binary, args=["--home", str(store.home), "mcp", "--host", host])
    # Install only this skill. Preserve every other host and skill setting.
    source = Path(__file__).parent / "data" / "skill"
    if not source.exists():
        source = Path(__file__).resolve().parents[2] / "skills/auto-lammps-mcp"
    require((source / "SKILL.md").is_file(), "安装包缺少科研工作指南", "package_incomplete", "安装包含 skill 的完整发布包")
    skill_dest = Path.home() / (".codex/skills" if host == "codex" else ".agents/skills") / "auto-lammps-mcp"
    if source.exists():
        skill_dest.mkdir(parents=True, exist_ok=True)
        for item in source.rglob("*"):
            if item.is_file():
                destination = skill_dest / item.relative_to(source)
                atomic_write(destination, item.read_bytes())
    if host == "codex":
        codex = codex_path()
        require(codex, "未找到 Codex 命令", "host_missing", "安装 Codex 或设置其真实程序路径")
        existing = subprocess.run([codex, "mcp", "get", "auto-lammps", "--json"], capture_output=True, text=True, timeout=20)
        if existing.returncode == 0:
            current = json.loads(existing.stdout).get("transport", {})
            require(current.get("command") == binary and current.get("args") == connection["args"], "已有 Auto-LAMMPS 连接指向另一安装", "host_connection_conflict", "核对原安装并通过平台设置选择要使用的版本")
        else:
            result = subprocess.run([codex, "mcp", "add", "auto-lammps", "--", binary, *connection["args"]], capture_output=True, text=True, timeout=20)
            require(result.returncode == 0, result.stderr, "host_configuration_failed", "检查平台的 MCP 配置权限")
    if host == "dsh":
        atomic_write(store.home / "dsh-mcp.json", encode({"mcpServers": {"auto-lammps": connection}}).encode())
        native = dict(id="mcp-auto-lammps", name="@deepseek-ai/dsh-mcp-client",
                      config=dict(serverName="auto-lammps", transport="stdio", **connection, failOnStartupError=True))
        atomic_write(store.home / "dsh-harness-entry.json", encode(native).encode())
    with store.transaction() as db:
        hosts = store.setting("hosts", {})
        hosts[host] = dict(connection=connection, version=__version__, configured_at=time.time(), skill=str(skill_dest),
                           next_action="在 DSH 的 MCP 设置中添加 stdio 连接，并使用回执中的 command 和 args" if host == "dsh" else "重新加载会话后调用 doctor")
        store.set_setting(db, "hosts", hosts)
        store.event(db, None, "installation_progress", dict(host=host, stage="host_configuration", version=__version__))
    if managed:
        require(platform.system() == "Darwin", "持久安装入口当前使用 macOS 系统服务", "platform", "使用 alm serve 启动服务")
        label = "org.auto-lammps.mcp." + __import__("hashlib").sha256(str(store.home).encode()).hexdigest()[:8]
        destination = Path.home() / "Library/LaunchAgents" / (label + ".plist")
        plist = dict(Label=label, ProgramArguments=[binary, "--home", str(store.home), "serve"],
                     RunAtLoad=True, KeepAlive=True, ThrottleInterval=10,
                     StandardOutPath=str(store.home / "logs/service.out"), StandardErrorPath=str(store.home / "logs/service.err"),
                     EnvironmentVariables={"PATH": str(Path(sys.executable).parent) + ":/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"})
        atomic_write(destination, plistlib.dumps(plist))
        domain = f"gui/{os.getuid()}"
        status = subprocess.run(["launchctl", "print", domain + "/" + label], capture_output=True)
        if status.returncode:
            result = subprocess.run(["launchctl", "bootstrap", domain, str(destination)], capture_output=True, text=True)
            require(result.returncode == 0, result.stderr, "service_install_failed", "检查 launchctl 的当前用户登录会话")
        with store.transaction() as db:
            store.set_setting(db, "launchd", dict(label=label, path=str(destination), version=__version__))
    receipt = doctor(store)
    atomic_write(store.home / "installation-receipt.json", encode(receipt).encode())
    return receipt

