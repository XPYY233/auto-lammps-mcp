"""Small CLI over the same service used by MCP."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from . import __version__
from .core import Fault, Store, encode
from .transport import call, serve


def parser():
    p = argparse.ArgumentParser(prog="alm")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--home", default=os.environ.get("ALM_HOME", str(Path.home() / ".local/share/auto-lammps-mcp")))
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    sub.add_parser("start")
    sub.add_parser("doctor")
    sub.add_parser("help")
    restore = sub.add_parser("restore")
    restore.add_argument("archive")
    restore.add_argument("--sha256", required=True)
    api = sub.add_parser("call")
    api.add_argument("method")
    api.add_argument("--json", default="{}", help="JSON object, or @path to a JSON file")
    mcp = sub.add_parser("mcp")
    mcp.add_argument("--host", default="unknown")
    install = sub.add_parser("install")
    install.add_argument("--host", choices=["codex", "dsh", "none"], default="none")
    install.add_argument("--no-launchd", action="store_true")
    install.add_argument("--dsh-runtime", help="Existing official dsh executable or built source directory")
    install.add_argument("--dsh-profile", default="web")
    install.add_argument("--dsh-home")
    return p


def main():
    args = parser().parse_args()
    home = str(Path(args.home).expanduser().resolve())
    try:
        if args.command == "restore":
            from .backup import restore_backup
            result = restore_backup(args.archive, args.sha256, home)
            print(encode(result))
            return
        elif args.command == "serve":
            serve(home)
            return
        if args.command == "mcp":
            from .mcp_server import run
            run(home, args.host)
            return
        if args.command == "install":
            from .installation import install
            result = install(Store(home), args.host, managed=not args.no_launchd,
                             dsh_runtime=args.dsh_runtime, dsh_profile=args.dsh_profile, dsh_home=args.dsh_home)
        elif args.command == "start":
            try:
                result = call(home, "doctor")
            except Fault as exc:
                if exc.code != "service_unavailable":
                    raise
                store = Store(home)
                with (store.home / "logs/service.log").open("ab") as log:
                    subprocess.Popen([sys.executable, "-m", "auto_lammps_mcp.cli", "--home", home, "serve"], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                for _ in range(50):
                    time.sleep(0.1)
                    try:
                        result = call(home, "doctor")
                        break
                    except Fault as retry:
                        if retry.code != "service_unavailable":
                            raise
                else:
                    raise Fault("service_start_failed", "后台未能启动", "读取安装目录 logs/service.log")
        elif args.command == "doctor":
            try:
                result = call(home, "doctor")
                result["daemon"]["responding"] = True
            except Fault as exc:
                if exc.code != "service_unavailable":
                    raise
                from .installation import doctor
                result = doctor(Store(home))
                result["daemon"]["responding"] = False
                result["next_actions"].insert(0, "运行 alm start 或 alm install")
        elif args.command == "help":
            from .service import Service
            result = Service(home).help()
        else:
            source = args.json
            values = json.loads(Path(source[1:]).read_text() if source.startswith("@") else source)
            result = call(home, args.method, values)
        print(encode(result))
    except (Fault, ValueError, OSError) as exc:
        error = exc.as_dict() if isinstance(exc, Fault) else Fault("command_failed", str(exc), "检查命令与本机环境").as_dict()
        print(encode(dict(error=error)))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

