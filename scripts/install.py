#!/usr/bin/env python3
"""Install a pinned source checkout or verified wheel in a versioned environment."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--wheel", type=Path)
    source.add_argument("--rollback", action="store_true", help="Reconnect the previous installed version while preserving research data")
    parser.add_argument("--sha256")
    parser.add_argument("--host", choices=["codex", "dsh", "none"], default="none")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local/share/auto-lammps-mcp-install")
    parser.add_argument("--home", type=Path, default=Path.home() / ".local/share/auto-lammps-mcp")
    parser.add_argument("--no-launchd", action="store_true")
    parser.add_argument("--dsh-runtime")
    parser.add_argument("--dsh-profile", default="web")
    parser.add_argument("--dsh-home")
    args = parser.parse_args()
    args.prefix = args.prefix.expanduser().resolve()
    args.home = args.home.expanduser().resolve()
    args.prefix.mkdir(parents=True, exist_ok=True)
    install_lock = (args.prefix / "installation.lock").open("a+")
    try:
        fcntl.flock(install_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise ValueError("另一安装流程正在运行，请读取安装进度后接续") from exc
    progress_path = args.prefix / "installation-progress.json"
    progress = json.loads(progress_path.read_text()) if progress_path.exists() else {"steps": {}}

    def record(stage, **values):
        progress["steps"][stage] = {"at": time.time(), **values}
        temporary = progress_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(progress, ensure_ascii=False, indent=2))
        os.replace(temporary, progress_path)

    if args.rollback:
        previous = progress["steps"].get("complete", {}).get("previous")
        if not previous:
            raise ValueError("没有保留的上一安装版本")
        environment = Path(previous).resolve()
        if environment.parent != args.prefix / "versions" or not (environment / "bin/alm").is_file():
            raise ValueError("上一安装版本目录不可用，请从固定源码或安装包恢复")
        record("rollback", environment=str(environment))
    else:
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
                subprocess.run(["uv", "venv", "--python", "3.13", str(environment)], check=True, stdout=sys.stderr)
            else:
                if sys.version_info < (3, 11):
                    raise ValueError("需要 Python 3.11 或更新版本；可安装 uv 后重试")
                subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True, stdout=sys.stderr)
        record("environment", python=str(python))
        command = ["uv", "pip", "install", "--python", str(python)] if shutil.which("uv") else [str(python), "-m", "pip", "install"]
        lock = target / "requirements.lock.txt" if args.source else environment / "requirements.lock.txt"
        if args.wheel:
            with zipfile.ZipFile(target) as archive:
                lock.write_bytes(archive.read("auto_lammps_mcp/data/requirements.lock.txt"))
        if lock and lock.exists():
            subprocess.run([*command, "-r", str(lock)], check=True, stdout=sys.stderr)
        subprocess.run([*command, str(target) + "[analysis]"], check=True, stdout=sys.stderr)
        (environment / "installation-source.json").write_text(json.dumps(dict(identity=actual, kind="wheel" if args.wheel else "source", installed_at=time.time())))
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
    if previous and Path(previous).resolve() == environment:
        previous = progress["steps"].get("complete", {}).get("previous")
    replacement = args.prefix / "current.next"
    replacement.unlink(missing_ok=True)
    replacement.symlink_to(environment)
    os.replace(replacement, current)
    record("complete", binary=str(binary), previous=previous, resume_prompt="继续配置 Auto-LAMMPS，从上次已经完成的步骤接着做。")
    print(json.dumps(dict(binary=str(binary), receipt=receipt, installation_progress=str(progress_path)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile, KeyError) as exc:
        print(json.dumps(dict(error=dict(code="installation_failed", message=str(exc), effect="已完成的步骤与研究数据保留", next_action="读取 installation-progress.json，从失败步骤继续")), ensure_ascii=False))
        raise SystemExit(1)
