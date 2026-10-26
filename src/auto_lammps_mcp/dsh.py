"""Connect an existing official DeepSeek Harness profile without copying credentials."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from .core import require
from .files import atomic_write


BEGIN = "# BEGIN AUTO-LAMMPS MCP"
END = "# END AUTO-LAMMPS MCP"


def runtime_command(runtime: str) -> list[str]:
    path = Path(runtime).expanduser().resolve()
    if path.is_dir():
        entry = path / "apps/cli/lib/bin.js"
        require(entry.is_file(), "该目录缺少已构建的 DeepSeek Harness CLI", "dsh_not_built", "使用已安装的 dsh 命令或完成官方构建")
        node = shutil.which("node")
        require(node, "未找到 Node.js", "node_missing", "安装 DeepSeek Harness 所需的 Node.js")
        return [node, str(entry)]
    require(path.is_file() and os.access(path, os.X_OK), "DeepSeek Harness 启动入口不可执行")
    return [str(path)]


def update_patch(original: str, entry: dict) -> str:
    require(original.count(BEGIN) == original.count(END) <= 1, "现有 Auto-LAMMPS 配置标记不完整")
    if BEGIN in original:
        original = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", original, flags=re.S)
    # Both an empty YAML list and an empty document can receive the first patch.
    if original.strip() in ("", "[]"):
        original = ""
    else:
        require(original.lstrip().startswith(("-", "#")), "DSH profile 应使用 YAML patch 列表", "dsh_patch_format", "保留原配置并按官方 profile 格式整理")
    block = "- insert: " + json.dumps([entry], ensure_ascii=False) + "\n"
    return original.rstrip() + ("\n\n" if original.strip() else "") + BEGIN + "\n" + block + END + "\n"


def connect(store, connection: dict, runtime: str, profile: str = "web", dsh_home: str | None = None) -> dict:
    require(re.fullmatch(r"[A-Za-z0-9_-]+", profile) and profile != "desktop", "请选择 DeepSeek Harness 的 web 或命名 profile")
    command = runtime_command(runtime)
    version = subprocess.run([*command, "--version"], capture_output=True, text=True, timeout=20)
    require(version.returncode == 0, "DeepSeek Harness 版本检查失败", "dsh_unavailable", "使用该安装的官方 CLI 检查启动状态")
    home = Path(dsh_home or os.environ.get("DSH_HOME") or Path.home() / ".dsh").expanduser().resolve()
    path = home / "profiles" / profile / "cordis.patch.yml"
    require(path.is_file(), "所选 DSH profile 尚未初始化", "dsh_profile_missing", "先用官方 dsh 启动一次该 profile，再接入")
    original = path.read_bytes()
    entry = dict(id="mcp-auto-lammps", name="@deepseek-ai/dsh-mcp-client",
                 config=dict(serverName="auto-lammps", transport="stdio", **connection, failOnStartupError=True))
    updated = update_patch(original.decode(), entry).encode()
    backup = store.home / "installation-backups" / ("dsh-" + profile + "-" + hashlib.sha256(original).hexdigest()[:16] + ".yml")
    atomic_write(backup, original)
    atomic_write(path, updated)
    try:
        check = subprocess.run([*command, "--profile", profile, "--dump-config"], env={**os.environ, "DSH_HOME": str(home)},
                               capture_output=True, text=True, timeout=45)
        # Dump output may contain model configuration. Persist neither stdout nor stderr.
        require(check.returncode == 0 and "mcp-auto-lammps" in check.stdout,
                "DSH 未能组合新增工具配置，已准备恢复原配置", "dsh_configuration_failed", "检查官方 profile 与 MCP 插件版本")
    except BaseException:
        if path.read_bytes() == updated:
            atomic_write(path, original)
        raise
    overlay = store.home / "dsh-auto-lammps.patch.yml"
    atomic_write(overlay, ("- insert: " + json.dumps([entry], ensure_ascii=False) + "\n").encode())
    return dict(runtime=command, version=version.stdout.strip(), profile=profile, profile_path=str(path),
                dsh_home=str(home), backup=str(backup), overlay=str(overlay), configured_at=time.time(),
                next_action="在 DeepSeek Harness 对话中调用 Auto-LAMMPS 的 doctor，读取实际连接回执")
