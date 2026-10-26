"""Transactions, revision fencing and durable operation receipts."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def identifier(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class Fault(Exception):
    def __init__(self, code, message, next_action, effect="rejected_before_action"):
        super().__init__(message)
        self.code, self.next_action, self.effect = code, next_action, effect

    def as_dict(self):
        return dict(code=self.code, message=str(self), next_action=self.next_action, effect=self.effect)


def require(condition, message, code="invalid_input", next_action="修正输入后重试"):
    if not condition:
        raise Fault(code, message, next_action)


class Store:
    def __init__(self, home=None):
        self.home = Path(home or os.environ.get("ALM_HOME", Path.home() / ".local/share/auto-lammps-mcp")).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.home, 0o700)
        for name in ("projects", "blobs", "exports", "logs"):
            (self.home / name).mkdir(exist_ok=True, mode=0o700)
        self.database = self.home / "tasks.sqlite3"
        marker = self.home / "auto-lammps-mcp.json"
        require(marker.exists() or not self.database.exists(), "目标目录已有其他任务数据库", "state_directory_conflict", "选择新版独立数据目录")
        if not marker.exists():
            marker.write_text(encode(dict(application="auto-lammps-mcp", schema=1)))
        else:
            require(json.loads(marker.read_text()).get("application") == "auto-lammps-mcp", "数据目录属于其他程序")
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            require(version in (0, 1), "数据库版本比当前软件新", "schema_version", "使用原版本启动服务")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, request TEXT NOT NULL,
                  requirements TEXT NOT NULL, constraints_json TEXT NOT NULL,
                  policy TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'draft',
                  revision INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                  id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, kind TEXT NOT NULL,
                  data TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS leases (
                  task_id TEXT PRIMARY KEY, owner TEXT NOT NULL, token TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots (
                  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, manifest TEXT NOT NULL,
                  digest TEXT NOT NULL, revision INTEGER NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS artifacts (
                  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
                  sources TEXT NOT NULL, requirements TEXT NOT NULL, checks TEXT NOT NULL,
                  stale INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, intent TEXT NOT NULL,
                  snapshot_id TEXT NOT NULL, environment TEXT NOT NULL, rules TEXT NOT NULL,
                  entry TEXT NOT NULL, state TEXT NOT NULL, job_id TEXT,
                  cluster TEXT NOT NULL, remote_dir TEXT NOT NULL,
                  condition_id TEXT NOT NULL, sample_id TEXT NOT NULL, purpose TEXT NOT NULL,
                  parent_id TEXT, evidence TEXT NOT NULL DEFAULT '{}', failures INTEGER NOT NULL DEFAULT 0,
                  created REAL NOT NULL, updated REAL NOT NULL,
                  UNIQUE(task_id, intent));
                CREATE TABLE IF NOT EXISTS queue (
                  id TEXT PRIMARY KEY, task_id TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL,
                  state TEXT NOT NULL, result TEXT, lease_until REAL, token TEXT,
                  created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS acknowledgements (
                  consumer TEXT PRIMARY KEY, seq INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS stages (
                  task_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
                  dependencies TEXT NOT NULL, specification TEXT NOT NULL,
                  state TEXT NOT NULL DEFAULT 'pending', operation_id TEXT, result TEXT,
                  created REAL NOT NULL, updated REAL NOT NULL, PRIMARY KEY(task_id,id));
                PRAGMA user_version=1;
            """)
        os.chmod(self.database, 0o600)

    def connect(self):
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, task_id, kind, data):
        db.execute("INSERT INTO events(task_id,kind,data,created) VALUES(?,?,?,?)", (task_id, kind, encode(data), time.time()))

    def mutate(self, op_id, payload, callback):
        require(isinstance(op_id, str) and 1 <= len(op_id) <= 200, "每次变更需要操作编号 op_id")
        fingerprint = digest(encode(payload).encode())
        with self.transaction() as db:
            previous = db.execute("SELECT * FROM operations WHERE id=?", (op_id,)).fetchone()
            if previous:
                require(previous["fingerprint"] == fingerprint, "操作编号已用于不同输入", "operation_conflict", "读取原操作或使用新的操作编号")
                return json.loads(previous["result"])
            result = callback(db)
            db.execute("INSERT INTO operations VALUES(?,?,?,?)", (op_id, fingerprint, encode(result), time.time()))
            return result

    def task(self, db, task_id, revision=None, token=None):
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        require(row is not None, "没有找到任务", "not_found", "使用 task_list 查找任务")
        if revision is not None:
            require(row["revision"] == revision, "任务已更新", "revision_conflict", "重新读取任务并检查差异")
        lease = db.execute("SELECT * FROM leases WHERE task_id=?", (task_id,)).fetchone()
        if token is not None:
            require(lease and lease["token"] == token and lease["expires"] > time.time(), "操作权已过期", "lease_expired", "重新取得操作权并读取任务")
        elif revision is not None:
            require(not lease or lease["expires"] <= time.time(), "另一执行者正在处理此任务", "lease_conflict", "读取进展或通过 task_control 交接")
        result = dict(row)
        for name in ("requirements", "constraints_json", "policy"):
            result[name] = json.loads(result[name])
        return result

    def bump(self, db, task_id):
        db.execute("UPDATE tasks SET revision=revision+1,updated=? WHERE id=?", (time.time(), task_id))

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, db, key, value):
        db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encode(value)))

    def root(self, task_id):
        require(task_id.startswith("task_") and task_id.replace("_", "").isalnum(), "任务编号无效")
        root = self.home / "projects" / task_id
        root.mkdir(exist_ok=True)
        return root

    def path(self, task_id, relative):
        root = self.root(task_id)
        candidate = root / relative
        require(bool(relative) and not Path(relative).is_absolute() and ".." not in Path(relative).parts, "使用项目内的相对路径")
        require(candidate.resolve().is_relative_to(root), "文件指向项目外部")
        return candidate

    def queue(self, db, task_id, kind, payload):
        key = identifier("work")
        now = time.time()
        db.execute("INSERT INTO queue(id,task_id,kind,payload,state,created,updated) VALUES(?,?,?,?,'pending',?,?)", (key, task_id, kind, encode(payload), now, now))
        self.event(db, task_id, "work_queued", dict(operation_id=key, kind=kind))
        return dict(operation_id=key, state="pending", next_action="operation_get")

