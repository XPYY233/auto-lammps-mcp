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
from .identity import runtime_identity, connection_status


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
    background_receipt = store.setting("background_receipt")
    if background_receipt and not background_receipt.get("verified"):
        background_receipt = None
    from .transport import socket_path
    source_file = Path(sys.executable).parent.parent / "installation-source.json"
    source = json.loads(source_file.read_text()) if source_file.is_file() else None
    receipt = dict(version=__version__, protocol=1, schema=1, python=sys.executable, package_path=str(Path(__file__).parent),
                   installation_source=source,
                   state_directory=str(store.home), platform=platform.system(),
                   daemon=dict(socket_exists=socket_path(store.home).exists(), managed=store.setting("launchd", False), heartbeat=store.setting("service_heartbeat")),
                   runtime=runtime_identity(), hosts=store.setting("hosts", {}),
                   host_calls={host: connection_status(record) for host, record in store.setting("host_calls", {}).items()}, recovery=store.setting("recovery"),
                   cluster_configured=bool(cluster), cluster_receipt=store.setting("cluster_receipt"),
                   environment_receipts=store.setting("environment_receipts", []),
                   background=dict(configured=background, executable=codex_path(), receipt=background_receipt),
                   available_ssh_aliases=sorted(set(aliases)))
    next_actions = []
    if not receipt["host_calls"]:
        next_actions.append("在当前 Agent 平台实际调用 doctor；配置文件存在还需要会话加载")
    for host in set(receipt["hosts"]) | set(receipt["host_calls"]):
        if host == "none":
            continue
        status = receipt["host_calls"].get(host) or connection_status({})
        if status["state"] != "current":
            next_actions.append(host + ": " + status["next_action"])
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


def install(store, host="none", managed=True, dsh_runtime=None, dsh_profile="web", dsh_home=None):
    require(host in ("codex", "dsh", "none"), "选择 codex、dsh 或 none")
    binary = str(Path(sys.executable).parent / "alm")
    require(Path(binary).is_file(), "命令行入口尚未安装", "missing_installation", "先安装当前版本 Python 包")
    connection = dict(command=binary, args=["--home", str(store.home), "mcp", "--host", host])
    # Install only this skill. Preserve every other host and skill setting.
    source = Path(__file__).parent / "data" / "skill"
    if not source.exists():
        source = Path(__file__).resolve().parents[2] / "skills/auto-lammps-mcp"
    require((source / "SKILL.md").is_file(), "安装包缺少科研工作指南", "package_incomplete", "安装包含 skill 的完整发布包")
    skill_dest = source if host == "none" else Path.home() / (".codex/skills" if host == "codex" else ".agents/skills") / "auto-lammps-mcp"
    if host != "none" and source.exists():
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
            if current.get("command") != binary or current.get("args") != connection["args"]:
                previous = store.setting("hosts", {}).get("codex", {}).get("connection", {})
                require(current.get("command") == previous.get("command") and current.get("args") == previous.get("args")
                        and not current.get("env") and not current.get("env_vars") and not current.get("cwd"),
                        "已有 Auto-LAMMPS 连接包含其他安装或自定义设置", "host_connection_conflict", "核对平台中的已有连接")
                with store.transaction() as db:
                    store.set_setting(db, "previous_codex_connection", previous)
                removed = subprocess.run([codex, "mcp", "remove", "auto-lammps"], capture_output=True, text=True, timeout=20)
                require(removed.returncode == 0, "未能更新原工具连接", "host_configuration_failed", "检查平台配置权限")
                added = subprocess.run([codex, "mcp", "add", "auto-lammps", "--", binary, *connection["args"]], capture_output=True, text=True, timeout=20)
                if added.returncode != 0:
                    subprocess.run([codex, "mcp", "add", "auto-lammps", "--", previous["command"], *previous["args"]], capture_output=True, text=True, timeout=20)
                    raise Fault("host_configuration_failed", "新版连接写入失败，已尝试恢复原连接", "检查平台配置权限和原连接")
        else:
            result = subprocess.run([codex, "mcp", "add", "auto-lammps", "--", binary, *connection["args"]], capture_output=True, text=True, timeout=20)
            require(result.returncode == 0, result.stderr, "host_configuration_failed", "检查平台的 MCP 配置权限")
    dsh_receipt = None
    if host == "dsh":
        atomic_write(store.home / "dsh-mcp.json", encode({"mcpServers": {"auto-lammps": connection}}).encode())
        native = dict(id="mcp-auto-lammps", name="@deepseek-ai/dsh-mcp-client",
                      config=dict(serverName="auto-lammps", transport="stdio", **connection, failOnStartupError=True))
        atomic_write(store.home / "dsh-harness-entry.json", encode(native).encode())
        if dsh_runtime:
            from .dsh import connect
            dsh_receipt = connect(store, connection, dsh_runtime, dsh_profile, dsh_home)
    with store.transaction() as db:
        hosts = store.setting("hosts", {})
        hosts[host] = dict(connection=connection, version=__version__, configured_at=time.time(), skill=str(skill_dest),
                           next_action="在 DSH 的 MCP 设置中添加 stdio 连接，并使用回执中的 command 和 args" if host == "dsh" else "重新加载会话后调用 doctor")
        if dsh_receipt:
            hosts[host].update(dsh_receipt)
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
        old_plist = destination.read_bytes() if destination.exists() else None
        old_values = plistlib.loads(old_plist) if old_plist else {}
        atomic_write(destination, plistlib.dumps(plist))
        domain = f"gui/{os.getuid()}"
        status = subprocess.run(["launchctl", "print", domain + "/" + label], capture_output=True)
        changed = old_values.get("ProgramArguments") != plist["ProgramArguments"]
        if not status.returncode:
            from .transport import call
            try:
                changed = changed or call(store.home, "doctor")["python"] != sys.executable
            except Fault:
                changed = True
        if not status.returncode and changed:
            with store.connect() as db:
                busy = db.execute("SELECT count(*) FROM queue WHERE state='running'").fetchone()[0]
            if busy:
                if old_plist:
                    atomic_write(destination, old_plist)
                raise Fault("service_busy", "后台正在处理任务，安装进度已保存", "等待当前处理结束后重试安装")
            stopped = subprocess.run(["launchctl", "bootout", domain + "/" + label], capture_output=True, text=True)
            require(stopped.returncode == 0, "旧后台未能退出", "service_upgrade_failed", "检查现有后台状态后继续安装")
            for _ in range(100):
                removed = subprocess.run(["launchctl", "print", domain + "/" + label], capture_output=True)
                if removed.returncode:
                    break
                time.sleep(0.1)
            status = stopped
            status.returncode = 1
        if status.returncode:
            result = subprocess.run(["launchctl", "bootstrap", domain, str(destination)], capture_output=True, text=True)
            if result.returncode:
                with store.transaction() as db:
                    store.event(db, None, "service_install_failed", dict(returncode=result.returncode, stderr=result.stderr, target=binary))
                if old_plist:
                    atomic_write(destination, old_plist)
                    subprocess.run(["launchctl", "bootstrap", domain, str(destination)], capture_output=True)
                raise Fault("service_install_failed", "后台启动失败，已保留原版本和安装进度: " + result.stderr.strip()[:300], "读取 service_install_failed 事件后接续")
        with store.transaction() as db:
            store.set_setting(db, "launchd", dict(label=label, path=str(destination), version=__version__))
        from .transport import call
        for _ in range(60):
            try:
                live = call(store.home, "doctor")
                if Path(live["python"]) == Path(sys.executable):
                    break
            except Fault:
                pass
            time.sleep(0.1)
        else:
            raise Fault("service_start_failed", "安装后的后台未返回对应版本回执", "读取服务日志，从安装进度接续")
    receipt = doctor(store)
    if managed:
        receipt["daemon"]["responding"] = True
    atomic_write(store.home / "installation-receipt.json", encode(receipt).encode())
    return receipt

