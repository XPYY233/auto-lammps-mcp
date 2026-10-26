#!/usr/bin/env python3
"""Install a pinned source checkout or verified wheel in a versioned environment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--wheel", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--host", choices=["codex", "dsh", "none"], default="none")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local/share/auto-lammps-mcp-install")
    parser.add_argument("--home", type=Path, default=Path.home() / ".local/share/auto-lammps-mcp")
    parser.add_argument("--no-launchd", action="store_true")
    parser.add_argument("--dsh-runtime")
    parser.add_argument("--dsh-profile", default="web")
    parser.add_argument("--dsh-home")
    args = parser.parse_args()
    args.prefix.mkdir(parents=True, exist_ok=True)
    progress_path = args.prefix / "installation-progress.json"
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {"steps": {}}

    def record(stage, **values):
        progress["steps"][stage] = {"at": time.time(), **values}
        temporary = progress_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(progress, ensure_ascii=False, indent=2))
        os.replace(temporary, progress_path)

    target = (args.source or args.wheel).expanduser().resolve()
    if args.wheel:
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if not args.sha256 or actual != args.sha256:
            raise ValueError("安装包摘要与发布记录不一致")
        identity = actual[:16]
    else:
        actual = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(target), "status", "--porcelain"], text=True)
        if dirty:
            raise ValueError("请先固定源代码提交，再从该版本安装")
        identity = actual[:16]
    record("source", identity=actual, path=str(target))
    environment = args.prefix / "versions" / identity
    python = environment / "bin/python"
    if not python.exists():
        if shutil.which("uv"):
            subprocess.run(["uv", "venv", "--python", "3.13", str(environment)], check=True)
        else:
            if sys.version_info < (3, 11):
                raise ValueError("需要 Python 3.11 或更新版本；可安装 uv 后重试")
            subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
    record("environment", python=str(python))
    command = ["uv", "pip", "install", "--python", str(python)] if shutil.which("uv") else [str(python), "-m", "pip", "install"]
    lock = target / "requirements.lock.txt" if args.source else None
    if lock and lock.exists():
        subprocess.run([*command, "-r", str(lock)], check=True)
    subprocess.run([*command, str(target)], check=True)
    record("package", completed=True)
    binary = environment / "bin/alm"
    install = [str(binary), "--home", str(args.home.resolve()), "install", "--host", args.host]
    if args.no_launchd:
        install.append("--no-launchd")
    if args.dsh_runtime:
        install.extend(["--dsh-runtime", args.dsh_runtime, "--dsh-profile", args.dsh_profile])
    if args.dsh_home:
        install.extend(["--dsh-home", args.dsh_home])
    result = subprocess.run(install, capture_output=True, text=True)
    try:
        receipt = json.loads(result.stdout)
    except ValueError:
        receipt = {"error": {"code": "installer_output_invalid", "message": "安装步骤未返回完整回执", "next_action": "使用本次 binary 的 install 命令读取错误并接续"}}
    if result.returncode or "error" in receipt:
        record("connection", completed=False, receipt=receipt, binary=str(binary))
        print(json.dumps(dict(binary=str(binary), receipt=receipt, installation_progress=str(progress_path)), ensure_ascii=False, indent=2))
        raise SystemExit(result.returncode or 1)
    record("connection", receipt=receipt)
    current = args.prefix / "current"
    previous = os.readlink(current) if current.is_symlink() else None
    replacement = args.prefix / "current.next"
    replacement.unlink(missing_ok=True)
    replacement.symlink_to(environment)
    os.replace(replacement, current)
    record("complete", binary=str(binary), previous=previous, resume_prompt="继续配置 Auto-LAMMPS，从上次已经完成的步骤接着做。")
    print(json.dumps(dict(binary=str(binary), receipt=receipt, installation_progress=str(progress_path)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
