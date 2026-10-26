"""Consistent SQLite backups and verified recovery into a separate state directory."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import tempfile
import time
import zipfile

from .core import Store, encode, identifier, require
from .files import file_hash


def create_backup(store, destination):
    destination = Path(destination).expanduser().resolve()
    require(not destination.exists() and not destination.is_relative_to(store.home), "备份应写入数据目录之外的新文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    token = identifier("backup")
    with store.transaction() as db:
        require(not db.execute("SELECT 1 FROM leases WHERE expires>?", (time.time(),)).fetchone(), "项目正在修改，等待当前操作释放后重试", "backup_busy", "释放当前任务操作权后重试备份")
        ids = [r[0] for r in db.execute("SELECT id FROM tasks")]
        for task_id in ids:
            db.execute("INSERT OR REPLACE INTO leases VALUES(?,?,?,?)", (task_id, "backup:" + token, token, time.time() + 3600))
    temporary_archive = destination.with_name(destination.name + "." + token + ".partial")
    try:
        with tempfile.TemporaryDirectory(prefix="alm-backup-") as temporary:
            root = Path(temporary)
            database = root / "tasks.sqlite3"
            with store.connect() as original, sqlite3.connect(database) as copy:
                original.backup(copy)
                require(copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "备份数据库校验失败")
            paths = [store.home / "auto-lammps-mcp.json"]
            for directory in [store.home / "projects" / task_id for task_id in ids] + [store.home / name for name in ("blobs", "exports", "logs")]:
                if directory.exists():
                    paths.extend(p for p in directory.rglob("*") if p.is_file())
            files = {"tasks.sqlite3": dict(sha256=file_hash(database), bytes=database.stat().st_size, executable=False)}
            with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(database, "state/tasks.sqlite3")
                renewed = time.monotonic()
                for path in paths:
                    if time.monotonic() - renewed > 30:
                        with store.transaction() as db:
                            db.execute("UPDATE leases SET expires=? WHERE token=?", (time.time() + 3600, token))
                        renewed = time.monotonic()
                    require(not path.is_symlink(), "备份中存在文件链接，请先将资源导入完整项目")
                    name = path.relative_to(store.home).as_posix()
                    before = file_hash(path)
                    info = dict(sha256=before, bytes=path.stat().st_size, executable=bool(path.stat().st_mode & 0o111))
                    archive.write(path, "state/" + name)
                    require(file_hash(path) == before, "备份期间文件发生变化", "file_changed", "保存当前编辑后重试备份")
                    files[name] = info
                metadata = dict(format="auto-lammps-backup", version=1, source_home=str(store.home), source_host=socket.gethostname(), created=time.time(), files=files)
                archive.writestr("backup-manifest.json", encode(metadata))
            temporary_archive.chmod(0o600)
            # Hard-link publication refuses a destination created concurrently.
            os.link(temporary_archive, destination)
        return dict(path=str(destination), sha256=file_hash(destination), files=len(files), tasks=len(ids))
    finally:
        temporary_archive.unlink(missing_ok=True)
        with store.transaction() as db:
            db.execute("DELETE FROM leases WHERE token=?", (token,))


def restore_backup(archive_path, expected_sha256, destination):
    archive_path = Path(archive_path).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    require(archive_path.is_file() and file_hash(archive_path) == expected_sha256, "备份摘要与回执不一致", "integrity_error", "选择回执对应的备份")
    require(not destination.exists(), "恢复位置应为尚不存在的新目录")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory(prefix=".alm-restore-", dir=destination.parent) as temporary:
        require(archive.getinfo("backup-manifest.json").file_size <= 64 * 1024 * 1024, "备份清单过大")
        metadata = json.loads(archive.read("backup-manifest.json"))
        require(metadata.get("format") == "auto-lammps-backup" and metadata.get("version") == 1, "备份格式或版本无效")
        files = metadata["files"]
        require("tasks.sqlite3" in files and "auto-lammps-mcp.json" in files, "备份缺少数据记录")
        names = archive.namelist()
        require(len(names) == len(set(names)) and set(names) == {"backup-manifest.json"} | {"state/" + name for name in files}, "备份文件与清单不一致")
        root = Path(temporary) / "state"
        root.mkdir(mode=0o700)
        for name, info in files.items():
            require(not Path(name).is_absolute() and ".." not in Path(name).parts and "\\" not in name, "备份路径无效")
            require(archive.getinfo("state/" + name).file_size == info["bytes"], "备份文件大小不一致")
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open("state/" + name) as incoming, path.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            require(file_hash(path) == info["sha256"], "备份文件损坏", "integrity_error", "选择完整备份")
            path.chmod(0o700 if info.get("executable") else 0o600)
        with sqlite3.connect(root / "tasks.sqlite3") as db:
            require(db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "恢复数据库校验失败")
            require(db.execute("PRAGMA user_version").fetchone()[0] == 1, "备份数据库需要对应版本的软件")
            db.execute("DELETE FROM leases")
            db.execute("DELETE FROM settings WHERE key LIKE 'process:%' OR key IN ('launchd','hosts','host_calls','background_receipt','service_heartbeat')")
            context = dict(source_home=metadata["source_home"], source_host=metadata["source_host"], backup_sha256=expected_sha256,
                           restored_at=time.time(), pending=True)
            db.execute("INSERT OR REPLACE INTO settings VALUES('recovery',?)", (encode(context),))
            db.execute("UPDATE queue SET state='interrupted',token=NULL,lease_until=NULL WHERE state='running'")
            db.execute("UPDATE runs SET state='unknown' WHERE state='dispatching'")
        # Preserve original record paths as history; task-relative files resolve in the new home.
        os.rename(root, destination)
    return dict(state_directory=str(destination), files=len(files), recovery_pending=True,
                next_action="启动服务读取任务和文件；确认原控制服务已停止后调用 recovery_activate 接续")
