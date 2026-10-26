"""Public operations shared by CLI, MCP, foreground agents and workers."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
import time

from . import __version__
from .core import Store, Fault, digest, encode, identifier, require
from .files import file_hash, invalidate, manifest, patch_file


def public(fn):
    fn.public = True
    return fn


class Service:
    def __init__(self, home=None):
        self.store = Store(home)

    def invoke(self, method, arguments):
        target = getattr(self, method, None)
        require(target and getattr(target, "public", False), "未知工具", "unknown_method", "调用 help 查看当前版本接口")
        try:
            inspect.signature(target).bind(**arguments)
        except TypeError as exc:
            raise Fault("invalid_arguments", str(exc), "调用 help 查看参数") from exc
        return target(**arguments)

    @public
    def help(self, method: str = "", offset: int = 0, limit: int = 10) -> dict:
        """Read one tool's complete signature, or page the generated catalog using next_offset until more is false."""
        require(0 <= offset and 1 <= limit <= 50, "offset 不能为负，limit 取 1 至 50")
        methods = {}
        for name in dir(self):
            fn = getattr(self, name)
            if getattr(fn, "public", False) and (not method or method == name):
                methods[name] = dict(signature=str(inspect.signature(fn)), description=inspect.getdoc(fn))
        if method:
            require(bool(methods), "未知工具名称", "unknown_method", "调用 help 分页查找工具名称")
            return dict(version=__version__, tools=methods)
        require(offset <= len(methods), "offset 超出工具目录")
        selected = {}
        for name, value in list(methods.items())[offset:offset + limit]:
            candidate = {**selected, name: value}
            if selected and len(json.dumps(candidate, ensure_ascii=False, indent=2)) > 4500:
                break
            selected = candidate
        next_offset = offset + len(selected)
        more = next_offset < len(methods)
        return dict(version=__version__, tools=selected, total=len(methods), offset=offset,
                    next_offset=next_offset, more=more,
                    next=dict(method="help", arguments=dict(offset=next_offset, limit=limit)) if more else None)

    @public
    def doctor(self) -> dict:
        """Inspect installation, persisted setup and actual tool/cluster/background receipts separately."""
        from .installation import doctor
        return doctor(self.store)

    @public
    def response_read(self, response_id: str, path: list[str] | None = None, offset: int = 0, limit: int = 20, text_limit: int = 4096) -> dict:
        """Read a stored complete host response by literal field path and next_offset; available across conversations and restarts."""
        from .responses import read_response
        return read_response(self.store, response_id, path or [], offset, limit, text_limit)

    @public
    def setup_configure(self, section: str, values: dict, op_id: str) -> dict:
        """Save shared setup progress. Sections: cluster, background, notifications. Store SSH aliases, never passwords. For one project's background preference use task_background instead of changing this shared setting."""
        from .installation import validate_config
        validate_config(section, values)
        def apply(db):
            if section == "cluster":
                db.execute("DELETE FROM settings WHERE key='cluster_receipt'")
            self.store.set_setting(db, section, values)
            self.store.event(db, None, "setup_updated", dict(section=section))
            return dict(section=section, saved=True, next_action="doctor")
        return self.store.mutate(op_id, dict(method="setup_configure", section=section, values=values), apply)

    @public
    def connection_check(self, op_id: str) -> dict:
        """Queue an SSH/Slurm and remote directory check. This does not run a simulation."""
        require(self.store.setting("cluster"), "请先配置集群")
        return self.store.mutate(op_id, dict(method="connection_check"), lambda db: self.store.queue(db, None, "connection_check", {}))

    @public
    def backup_create(self, destination: str, op_id: str) -> dict:
        """Queue a verified database/files backup outside the state directory. Release task leases before starting."""
        path = Path(destination).expanduser().resolve()
        def apply(db):
            require(not path.exists() and not path.is_relative_to(self.store.home), "请选择数据目录外的新备份文件")
            return self.store.queue(db, None, "backup", dict(destination=str(path)))
        return self.store.mutate(op_id, dict(method="backup_create", destination=str(path)), apply)

    @public
    def recovery_activate(self, previous_controller_stopped: bool, op_id: str) -> dict:
        """Resume a restored service after confirming the original controller is stopped. Keep original job identities."""
        import socket
        recovery = self.store.setting("recovery", {})
        require(previous_controller_stopped is True, "先停止原任务服务，再接续恢复目录")
        if recovery.get("pending") and recovery["source_host"] == socket.gethostname() and Path(recovery["source_home"]) != self.store.home:
            from .transport import call
            try:
                call(recovery["source_home"], "doctor")
            except Fault as exc:
                require(exc.code == "service_unavailable", "原服务状态尚未确认，请先核对")
            else:
                raise Fault("controller_active", "原任务服务仍在运行", "停止原服务后再接续恢复目录")
        def apply(db):
            require(recovery.get("pending"), "当前数据目录没有待接续的恢复")
            self.store.set_setting(db, "recovery", {**recovery, "pending": False, "activated_at": time.time()})
            self.store.event(db, None, "recovery_activated", dict(backup_sha256=recovery["backup_sha256"]))
            return dict(activated=True)
        return self.store.mutate(op_id, dict(method="recovery_activate", previous_controller_stopped=True), apply)

    @public
    def environment_verify(self, run_id: str, evidence_path: str, expected_text: str, op_id: str) -> dict:
        """Verify a completed environment_check run using collected engine output and a scientific verification marker."""
        run = self.run_get(run_id)
        require(run["purpose"] == "environment_check" and run["state"] == "completed" and run["evidence"].get("exit_code") == "0:0", "环境验证作业尚未成功结束")
        require(evidence_path.startswith("runs/" + run_id + "/") and bool(expected_text), "证据必须来自该作业回收目录")
        file = self.store.path(run["task_id"], evidence_path)
        require(file.is_file() and expected_text in file.read_text(errors="replace"), "计算节点输出未包含预期验证证据")
        from .science import thermo_blocks
        import re
        log = file.read_text(errors="replace")
        loops = re.findall(r"Loop time of .* on (\d+) procs for (\d+) steps", log)
        blocks = thermo_blocks(file)
        require("LAMMPS" in log and "ERROR" not in log and loops and any(int(n) > 0 for _, n in loops) and blocks and any(len(b["rows"]) >= 2 for b in blocks), "环境验证需要真实计算步、热力学输出和正常结束记录")
        receipt = dict(run_id=run_id, environment=run["environment"], evidence=evidence_path, sha256=file_hash(file), expected_text=expected_text, confirmed_at=time.time())
        def apply(db):
            self.store.set_setting(db, "environment:" + run["environment"]["fingerprint"], receipt)
            receipts = self.store.setting("environment_receipts", [])
            receipts.append(receipt)
            self.store.set_setting(db, "environment_receipts", receipts)
            self.store.event(db, run["task_id"], "environment_verified", receipt)
            return receipt
        return self.store.mutate(op_id, dict(method="environment_verify", run_id=run_id, evidence_path=evidence_path, expected_text=expected_text), apply)

    @public
    def task_create(self, title: str, request: str, requirements: list[dict], op_id: str, constraints: dict | None = None, mode: str = "research", policy_source: str = "user request") -> dict:
        """Create a project from original words and deliverables. Each requirement needs id and description; use kind='archive' for an exported ZIP, otherwise kind='file'."""
        require(mode in ("research", "author_reference", "formal_b", "historical"), "未知任务规则")
        require(title.strip() and request.strip() and requirements, "需要任务名称、原始需求和成果清单")
        ids = [item.get("id") for item in requirements]
        require(all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids), "成果编号应唯一且非空")
        require(all(item.get("description") for item in requirements), "每项成果需要描述")
        require(all(item.get("kind", "file") in ("file", "archive") for item in requirements), "成果类型为 file 或 archive")
        def apply(db):
            task_id, now = identifier("task"), time.time()
            policy = dict(mode=mode, submission_limit=2 if mode == "formal_b" else None,
                          cores=32, memory_mib=8192, wall_seconds=604800, concurrency=1,
                          source=policy_source, effective_at=now, authorized=False)
            db.execute("INSERT INTO tasks(id,title,request,requirements,constraints_json,policy,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                       (task_id, title, request, encode(requirements), encode(constraints or {}), encode(policy), now, now))
            self.store.event(db, task_id, "task_created", dict(request=request, requirements=requirements, policy=policy))
            return self.store.task(db, task_id)
        return self.store.mutate(op_id, dict(method="task_create", title=title, request=request, requirements=requirements, constraints=constraints, mode=mode, policy_source=policy_source), apply)

    @public
    def task_list(self, query: str = "", limit: int = 30) -> dict:
        """Find tasks by title or ID. Resolve ambiguous names before changing a task."""
        require(1 <= limit <= 100, "limit 应为 1 到 100")
        with self.store.connect() as db:
            rows = db.execute("SELECT id,title,state,revision,created,updated FROM tasks WHERE instr(title,?)>0 OR id=? ORDER BY updated DESC LIMIT ?", (query, query, limit)).fetchall()
        return dict(tasks=[dict(row) for row in rows])

    @public
    def task_get(self, task_id: str, include_details: bool = False) -> dict:
        """Resume a project with original goals, rules, current lease owner and compact progress. executor identifies its owner without sharing write tokens; active_background identifies ongoing automatic recovery even while it awaits queued work. Follow operation_get before starting a competing repair. Use task_records for paged history. include_details=True returns the complete record for export."""
        with self.store.connect() as db:
            result = self.store.task(db, task_id)
            lease = db.execute("SELECT owner,expires FROM leases WHERE task_id=? AND expires>?", (task_id, time.time())).fetchone()
            result["executor"] = None
            if lease:
                owner = lease["owner"]
                kind = "worker" if owner.startswith("worker:") else "agent"
                executor = dict(owner=owner, kind=kind, expires=lease["expires"])
                if kind == "worker":
                    operation_id = owner.split(":", 1)[1]
                    operation = db.execute("SELECT state,updated FROM queue WHERE id=? AND task_id=?", (operation_id, task_id)).fetchone()
                    executor.update(operation_id=operation_id, next_action="operation_get")
                    if operation:
                        executor.update(dict(operation))
                result["executor"] = executor
            result["active_background"] = [dict(row) for row in db.execute("SELECT id AS operation_id,state,updated FROM queue WHERE task_id=? AND kind='decision' AND state='running' ORDER BY created", (task_id,))]
            result["automation"] = self.background_status(db, task_id)
            result["runs"] = [dict(row) for row in db.execute("SELECT * FROM runs WHERE task_id=? ORDER BY created", (task_id,))]
            result["artifacts"] = [dict(row) for row in db.execute("SELECT * FROM artifacts WHERE task_id=? ORDER BY created", (task_id,))]
            work_columns = "*" if include_details else "id,kind,state,result,updated"
            recent_limit = "" if include_details else " LIMIT 5"
            result["work"] = [dict(row) for row in db.execute("SELECT " + work_columns + " FROM queue WHERE task_id=? ORDER BY created DESC" + recent_limit, (task_id,))]
            result["events"] = [dict(row) for row in db.execute("SELECT * FROM events WHERE task_id=? ORDER BY seq DESC" + recent_limit, (task_id,))]
            result["stages"] = [dict(row) for row in db.execute("SELECT * FROM stages WHERE task_id=? ORDER BY created,id", (task_id,))]
            result["deliveries"] = [json.loads(row[0]) for row in db.execute("SELECT value FROM settings WHERE key LIKE ?", ("delivery:" + task_id + ":%",))]
            result["record_counts"] = {name: db.execute("SELECT count(*) FROM " + table + " WHERE task_id=?", (task_id,)).fetchone()[0] for name, table in {"runs":"runs", "artifacts":"artifacts", "work":"queue", "events":"events", "stages":"stages"}.items()}
            result["record_counts"]["deliveries"] = len(result["deliveries"])
        result["project_path"] = str(self.store.root(task_id))
        for run in result["runs"]:
            for key in ("environment", "rules", "evidence"):
                run[key] = json.loads(run[key])
        for artifact in result["artifacts"]:
            for key in ("sources", "requirements", "checks"):
                artifact[key] = json.loads(artifact[key])
        result["next_action"] = {
            "completed": "查看已登记成果，或提出追加分析",
            "paused": "后续派发已暂停；查看当前作业，准备继续时恢复任务",
            "stopping": "停止意图已保存，正在取消当前作业；读取进展核对结果",
            "stopped": "停止意图已保存；核对作业取消结果，可继续读取和整理已有数据",
        }.get(result["state"], "检查当前阶段与未交付成果，继续相关工作")
        from .records import restored_paths, task_summary, decoded_record
        if include_details:
            for name in ("runs", "work", "events", "stages"):
                result[name] = [decoded_record(row) for row in result[name]]
        return restored_paths(self.store, result if include_details else task_summary(result))

    def background_status(self, db, task_id):
        def setting(key, default):
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default
        shared = bool(setting("background", {}).get("enabled"))
        local = setting("task_background:" + task_id, {}).get("enabled", True)
        return dict(enabled=local, service_enabled=shared, effective_enabled=local and shared)

    @public
    def task_background(self, task_id: str, revision: int, enabled: bool, op_id: str, token: str | None = None) -> dict:
        """Enable or hold future automatic Codex continuations for one project. Running continuations finish their current turn; this does not cancel them or active analyses/jobs. Read active_background before taking over. To interrupt immediately use task_control pause and read current work before resuming. Pending decisions remain saved. Shared setup and other projects stay unchanged."""
        require(type(enabled) is bool, "enabled 需要 true 或 false")
        def apply(db):
            self.store.task(db, task_id, revision, token)
            self.store.set_setting(db, "task_background:" + task_id, dict(enabled=enabled))
            self.store.bump(db, task_id)
            active = [row[0] for row in db.execute("SELECT id FROM queue WHERE task_id=? AND kind='decision' AND state='running'", (task_id,))]
            status = self.background_status(db, task_id)
            self.store.event(db, task_id, "task_background_updated", dict(**status, active_background=active))
            return dict(task_id=task_id, revision=revision + 1, **status, active_background=active,
                        next_action="跟进正在进行的接续；本设置控制后续自动启动" if active else "task_get")
        return self.store.mutate(op_id, dict(method="task_background", task_id=task_id, revision=revision, enabled=enabled, token=token), apply)

    @public
    def task_records(self, task_id: str, kind: str, offset: int = 0, limit: int = 10) -> dict:
        """Read runs, artifacts, work or stages without repeating the project. Large work records link to operation_read for full details. Follow next_offset when more is true."""
        tables = {"runs": "runs", "artifacts": "artifacts", "work": "queue", "stages": "stages"}
        require(kind in tables and offset >= 0 and 1 <= limit <= 50, "请选择 runs、artifacts、work 或 stages，以及有效分页范围")
        with self.store.connect() as db:
            self.store.task(db, task_id)
            rows = db.execute("SELECT * FROM " + tables[kind] + " WHERE task_id=? ORDER BY created,id LIMIT ? OFFSET ?", (task_id, limit+1, offset)).fetchall()
        from .records import restored_paths, decoded_record
        records = [decoded_record(row) for row in rows[:limit]]
        records = restored_paths(self.store, records)
        if kind == "work":
            from .operation_views import operation_summary, size
            selected = []
            for record in records:
                summary = operation_summary(record)
                if selected and size(dict(records=selected + [summary])) > 5500:
                    break
                selected.append(summary)
            records = selected
        return dict(records=records, next_offset=offset+len(records), more=len(rows)>len(records))

    @public
    def task_claim(self, task_id: str, owner: str, seconds: int = 300, token: str | None = None) -> dict:
        """Acquire or renew the single writer lease. An expired token can never write."""
        require(30 <= seconds <= 900 and owner.strip(), "操作权需要执行者名称与 30–900 秒有效期")
        with self.store.transaction() as db:
            self.store.task(db, task_id)
            row = db.execute("SELECT * FROM leases WHERE task_id=?", (task_id,)).fetchone()
            now = time.time()
            if row and row["expires"] > now:
                require(token == row["token"] and owner == row["owner"], "任务已有执行者", "lease_conflict", "查看当前进展或暂停并交接")
            else:
                require(token is None, "旧操作权已过期", "lease_expired", "重新获取操作权并读取任务")
                token = identifier("lease")
            db.execute("INSERT OR REPLACE INTO leases VALUES(?,?,?,?)", (task_id, owner, token, now + seconds))
            return dict(task_id=task_id, owner=owner, token=token, expires=now + seconds)

    @public
    def task_release(self, task_id: str, token: str) -> dict:
        """Release this executor's lease."""
        with self.store.transaction() as db:
            self.store.task(db, task_id, token=token)
            db.execute("DELETE FROM leases WHERE task_id=? AND token=?", (task_id, token))
        return dict(released=True)

    @public
    def task_update(self, task_id: str, revision: int, changes: dict, reason: str, op_id: str, token: str | None = None) -> dict:
        """Update goals, constraints or policy with an explicit reason; immutable original request is preserved."""
        require(set(changes) <= {"title", "requirements", "constraints", "policy"} and reason.strip(), "变更需要有效字段和原因")
        def apply(db):
            task = self.store.task(db, task_id, revision, token)
            for name, value in changes.items():
                column = "constraints_json" if name == "constraints" else name
                if name == "policy":
                    require(set(value) <= {"authorized", "authorization", "source", "cores", "memory_mib", "wall_seconds", "submission_limit"}, "规则字段无效")
                    value = {**task["policy"], **value, "effective_at": time.time()}
                    require(value["cores"] == 32 and value["memory_mib"] == 8192 and 1 <= value["wall_seconds"] <= 604800, "当前执行配置为 32 核、8 GiB，时限最长七天")
                    require(value["submission_limit"] is None or type(value["submission_limit"]) is int and value["submission_limit"] > 0, "提交上限应为正整数或空")
                    require(not value["authorized"] or bool(value.get("authorization")), "授权需要保存用户原话")
                if name == "requirements":
                    ids = [item.get("id") for item in value]
                    require(value and all(ids) and len(ids) == len(set(ids)) and all(item.get("description") for item in value), "成果清单需要唯一编号和描述")
                    require(all(item.get("kind", "file") in ("file", "archive") for item in value), "成果类型为 file 或 archive")
                    previous = {item["id"]: item["description"] for item in task["requirements"]}
                    current = {item["id"]: item["description"] for item in value}
                    require(all(current.get(key) == text for key, text in previous.items()), "原始成果要求需要保留；补充要求可追加新编号", "requirement_preservation", "保留原要求并登记新的研究范围")
                db.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (value if name == "title" else encode(value), task_id))
            self.store.bump(db, task_id)
            if "policy" in changes:
                affected = [row[0] for row in db.execute("SELECT id FROM runs WHERE task_id=? AND state='pending'", (task_id,))]
                db.execute("UPDATE runs SET state='prepared',updated=? WHERE task_id=? AND state='pending'", (time.time(), task_id))
                if affected:
                    self.store.event(db, task_id, "pending_rules_updated", dict(run_ids=affected, next_action="按当前规则调用 run_submit；准备中的旧输入不会派发"))
            if "constraints" in changes and changes["constraints"] != task["constraints_json"]:
                db.execute("UPDATE artifacts SET stale=1 WHERE task_id=?", (task_id,))
                db.execute("UPDATE runs SET state='cancelled_before_dispatch' WHERE task_id=? AND state IN ('prepared','pending')", (task_id,))
            elif "requirements" in changes:
                old = {r["id"]: r for r in task["requirements"]}
                changed_ids = {r["id"] for r in changes["requirements"] if old.get(r["id"]) != r}
                for artifact in db.execute("SELECT id,requirements FROM artifacts WHERE task_id=?", (task_id,)):
                    if changed_ids & set(json.loads(artifact["requirements"])):
                        db.execute("UPDATE artifacts SET stale=1 WHERE id=?", (artifact["id"],))
            if "constraints" in changes or "requirements" in changes:
                db.execute("UPDATE tasks SET state='draft' WHERE id=? AND state='completed'", (task_id,))
            self.store.event(db, task_id, "task_updated", dict(changes=changes, reason=reason, previous_revision=revision))
            return self.store.task(db, task_id)
        return self.store.mutate(op_id, dict(method="task_update", task_id=task_id, revision=revision, changes=changes, reason=reason, token=token), apply)

    @public
    def task_control(self, task_id: str, revision: int, action: str, op_id: str) -> dict:
        """Pause future dispatch, resume, or stop and cancel active jobs. Persists intent before cancellation."""
        require(action in ("pause", "resume", "stop"), "action 应为 pause、resume 或 stop")
        def apply(db):
            task = self.store.task(db, task_id)
            require(task["revision"] == revision, "任务已更新", "revision_conflict", "重新读取任务")
            state = {"pause": "paused", "resume": "active", "stop": "stopping"}[action]
            db.execute("UPDATE tasks SET state=? WHERE id=?", (state, task_id))
            db.execute("DELETE FROM leases WHERE task_id=?", (task_id,))
            self.store.bump(db, task_id)
            self.store.event(db, task_id, action + "_requested", dict(previous_state=task["state"]))
            if action == "stop":
                db.execute("UPDATE runs SET state='cancelled_before_dispatch' WHERE task_id=? AND state IN ('prepared','pending')", (task_id,))
                self.store.queue(db, task_id, "cancel", {})
            return dict(task_id=task_id, state=state, revision=revision + 1, running_jobs="继续运行" if action == "pause" else "查看 run_get 的实际状态")
        return self.store.mutate(op_id, dict(method="task_control", task_id=task_id, revision=revision, action=action), apply)

    @public
    def events(self, after: int = 0, task_id: str | None = None, limit: int = 100, consumer: str | None = None) -> dict:
        """Read durable progress since a cursor; optional consumer resumes its unread cursor without marking read."""
        require(0 <= after and 1 <= limit <= 500, "分页参数无效")
        with self.store.connect() as db:
            if consumer:
                ack = db.execute("SELECT seq FROM acknowledgements WHERE consumer=?", (consumer,)).fetchone()
                after = max(after, ack[0] if ack else 0)
            rows = db.execute("SELECT * FROM events WHERE seq>? AND (? IS NULL OR task_id=?) ORDER BY seq LIMIT ?", (after, task_id, task_id, limit + 1)).fetchall()
        items = [{**dict(row), "data": json.loads(row["data"])} for row in rows[:limit]]
        return dict(events=items, next_cursor=items[-1]["seq"] if items else after, more=len(rows) > limit)

    @public
    def events_ack(self, consumer: str, through: int) -> dict:
        """Mark displayed events as read for one platform/session."""
        with self.store.transaction() as db:
            maximum = db.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
            require(0 <= through <= maximum and consumer.strip(), "事件游标无效")
            db.execute("INSERT INTO acknowledgements VALUES(?,?) ON CONFLICT(consumer) DO UPDATE SET seq=max(seq,excluded.seq)", (consumer, through))
        return dict(consumer=consumer, through=through)

    @public
    def project_import(self, task_id: str, revision: int, source: str, op_id: str, token: str | None = None) -> dict:
        """Import a directory or exported ZIP into the task's project_path, retaining relative paths, binary files and helpers.

        Existing identical files are reused; differing files cause a conflict, never overwrite. ZIP hashes and original records are checked.
        Returns operation_id immediately. Release any task_claim before waiting on operation_get; read task_get for the new revision after completion.
        """
        incoming = Path(source).expanduser()
        require(incoming.is_dir() or incoming.is_file() and incoming.suffix.lower() == ".zip", "请选择项目目录或导出的研究 ZIP 包")
        def apply(db):
            self.store.task(db, task_id, revision, token)
            self.store.bump(db, task_id)
            return self.store.queue(db, task_id, "import", dict(source=str(Path(source).expanduser().resolve())))
        return self.store.mutate(op_id, dict(method="project_import", task_id=task_id, revision=revision, source=source, token=token), apply)

    @public
    def file_read(self, task_id: str, path: str, offset: int = 0, limit: int = 65536) -> dict:
        """Read bytes as UTF-8 text with byte offsets, content hash and explicit continuation. Binary files use artifact paths."""
        require(offset >= 0 and 1 <= limit <= 262144, "读取范围无效")
        with self.store.connect() as db:
            self.store.task(db, task_id)
        target = self.store.path(task_id, path)
        require(target.is_file(), "文件不存在", "not_found", "读取项目清单")
        with target.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(limit)
        size = target.stat().st_size
        return dict(path=path, text=data.decode("utf-8", errors="replace"), offset=offset, next_offset=offset + len(data), bytes=size, truncated=offset + len(data) < size, sha256=file_hash(target))

    @public
    def artifact_preview(self, task_id: str, path: str) -> dict:
        """Show an existing file with its hash, artifact sources and freshness. MCP embeds supported images directly."""
        import mimetypes
        with self.store.connect() as db:
            self.store.task(db, task_id)
            registered = db.execute("SELECT * FROM artifacts WHERE task_id=? AND path=? ORDER BY created DESC LIMIT 1", (task_id, path)).fetchone()
        target = self.store.path(task_id, path)
        require(target.is_file(), "成果文件不存在", "not_found", "读取任务中的成果路径")
        result = dict(path=path, absolute_path=str(target), bytes=target.stat().st_size, sha256=file_hash(target),
                      mime_type=mimetypes.guess_type(path)[0] or "application/octet-stream", registered=registered is not None)
        if registered:
            sources = json.loads(registered["sources"])
            from .files import preview_stale
            stale = preview_stale(self.store, task_id, path)
            result.update(sources=sources, stale=stale, requirements=json.loads(registered["requirements"]))
        return result

    @public
    def file_patch(self, task_id: str, revision: int, path: str, expected_sha256: str | None, old: str, new: str, op_id: str, token: str | None = None) -> dict:
        """Edit a single exact fragment; new files use null expected_sha256 and empty old. Returns diff and derived hash."""
        def apply(db):
            self.store.task(db, task_id, revision, token)
            result = patch_file(self.store, task_id, path, expected_sha256, old, new)
            self.store.bump(db, task_id)
            from .files import invalidate_paths
            invalidate_paths(self.store, db, task_id, {path})
            self.store.event(db, task_id, "file_changed", result)
            return {**result, "revision": revision + 1}
        return self.store.mutate(op_id, dict(method="file_patch", task_id=task_id, revision=revision, path=path, expected_sha256=expected_sha256, old=old, new=new, token=token), apply)

    @public
    def project_snapshot(self, task_id: str, revision: int, op_id: str, token: str | None = None, paths: list[str] | None = None) -> dict:
        """Queue content-addressed snapshot of native files, including edits made by the agent's ordinary file tools."""
        if paths is not None:
            require(paths, "输入路径清单不能为空")
            for path in paths:
                require(self.store.path(task_id, path).exists(), "快照输入路径不存在: " + path)
        def apply(db):
            self.store.task(db, task_id, revision, token)
            self.store.bump(db, task_id)
            return self.store.queue(db, task_id, "snapshot", dict(revision=revision + 1, paths=paths))
        return self.store.mutate(op_id, dict(method="project_snapshot", task_id=task_id, revision=revision, token=token, paths=paths), apply)

    @public
    def operation_get(self, operation_id: str, include_details: bool = False) -> dict:
        """Read work progress/result after any conversation. Large results preserve IDs and link to operation_read; follow its paths and cursors for every detail. include_details=True returns the full record for local consumers."""
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM queue WHERE id=?", (operation_id,)).fetchone()
            require(row, "没有找到后台操作", "not_found", "读取任务记录")
        result = dict(row)
        for field in ("payload", "result"):
            result[field] = json.loads(result[field]) if result[field] else None
        from .records import restored_paths
        from .operation_views import operation_summary
        result = restored_paths(self.store, result)
        return result if include_details else operation_summary(result)

    @public
    def operation_read(self, operation_id: str, path: list[str] | None = None, offset: int = 0, limit: int = 20, text_limit: int = 4096) -> dict:
        """Read lossless pages of a work record. path=['result','files'] reads a snapshot manifest; child paths preserve literal filenames. Objects/arrays return items; long values link to deeper paths. Text uses character offsets. Follow next_offset while more is true."""
        from .operation_views import operation_page
        return operation_page(self.operation_get(operation_id, include_details=True), path or [], offset, limit, text_limit)

    @public
    def operation_retry(self, operation_id: str, op_id: str) -> dict:
        """Resume failed/interrupted non-submission work. Creates a separate attempt and preserves the original failure."""
        def apply(db):
            row = db.execute("SELECT * FROM queue WHERE id=?", (operation_id,)).fetchone()
            require(row and row["state"] in ("failed", "interrupted"), "仅接续已失败或中断的后台操作")
            payload = json.loads(row["payload"])
            payload["previous_attempt"] = operation_id
            return self.store.queue(db, row["task_id"], row["kind"], payload)
        return self.store.mutate(op_id, dict(method="operation_retry", operation_id=operation_id), apply)

    @public
    def run_prepare(self, task_id: str, revision: int, snapshot_id: str, entry: str, intent: str, condition_id: str, sample_id: str, op_id: str, purpose: str = "research", parent_id: str | None = None, token: str | None = None) -> dict:
        """Freeze a Slurm execution intent, input, environment and rules. Native entry is a project-relative shell script."""
        require(purpose in ("research", "retry", "continuation", "environment_check"), "执行目的无效")
        require(intent and condition_id and sample_id, "需要执行意图、工况和独立样本编号")
        config = self.store.setting("cluster")
        require(config, "尚未配置集群", "setup_required", "调用 setup_configure")
        def apply(db):
            task = self.store.task(db, task_id, revision, token)
            snap = db.execute("SELECT * FROM snapshots WHERE id=? AND task_id=?", (snapshot_id, task_id)).fetchone()
            require(snap, "没有找到输入快照")
            require(entry in json.loads(snap["manifest"]), "入口脚本不在快照中")
            require(task["state"] not in ("paused", "stopping", "stopped"), "任务已暂停或停止", "task_paused", "读取任务并决定是否恢复")
            if purpose in ("retry", "continuation"):
                parent = db.execute("SELECT * FROM runs WHERE id=? AND task_id=?", (parent_id, task_id)).fetchone()
                require(parent and parent["state"] in ("completed", "failed", "cancelled", "timeout"), "重试或续算需要已核对结束的原作业")
            else:
                require(parent_id is None, "只有重试和续算使用 parent_id")
            duplicate = db.execute("SELECT * FROM runs WHERE task_id=? AND (intent=? OR (snapshot_id IN (SELECT id FROM snapshots WHERE digest=? AND task_id=?) AND entry=? AND condition_id=? AND sample_id=? AND purpose=? AND parent_id IS ?))", (task_id, intent, snap["digest"], task_id, entry, condition_id, sample_id, purpose, parent_id)).fetchone()
            if duplicate:
                return dict(duplicate)
            run_id, now = identifier("run"), time.time()
            environment = {key: config[key] for key in ("ssh_alias", "remote_root", "account", "partition", "environment_script")}
            environment["fingerprint"] = digest(encode(environment).encode())
            remote = config["remote_root"].rstrip("/") + "/" + task_id + "/" + run_id
            db.execute("INSERT INTO runs(id,task_id,intent,snapshot_id,environment,rules,entry,state,cluster,remote_dir,condition_id,sample_id,purpose,parent_id,created,updated) VALUES(?,?,?,?,?,?,?,'prepared',?,?,?,?,?,?,?,?)",
                       (run_id, task_id, intent, snapshot_id, encode(environment), encode(task["policy"]), entry, config["ssh_alias"], remote, condition_id, sample_id, purpose, parent_id, now, now))
            self.store.bump(db, task_id)
            self.store.event(db, task_id, "run_prepared", dict(run_id=run_id, rules=task["policy"], snapshot_id=snapshot_id))
            return dict(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
        return self.store.mutate(op_id, dict(method="run_prepare", task_id=task_id, revision=revision, snapshot_id=snapshot_id, entry=entry, intent=intent, condition_id=condition_id, sample_id=sample_id, purpose=purpose, parent_id=parent_id, token=token), apply)

    @public
    def run_submit(self, run_id: str, revision: int, op_id: str, token: str | None = None) -> dict:
        """Persist submission intent and return immediately. Repeated calls return the same run; unknown dispatch reconciles first."""
        def apply(db):
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            require(row, "找不到执行记录")
            if row["state"] != "prepared":
                return dict(row)
            task = self.store.task(db, row["task_id"], revision, token)
            require(task["state"] not in ("paused", "stopping", "stopped"), "任务已暂停或停止")
            policy = task["policy"]
            require(policy["mode"] != "formal_b", "正式 B 需要独立执行身份与访问隔离配置", "isolation_required", "完成独立评测执行器验收后接入正式 B")
            require(policy.get("authorized"), "等待确认计算内容和资源", "authorization_needed", "展示方案并保存用户的开始指令")
            used = db.execute("SELECT count(*) FROM runs WHERE task_id=? AND state NOT IN ('prepared','cancelled_before_dispatch','staging_failed')", (task["id"],)).fetchone()[0]
            limit = policy.get("submission_limit")
            require(limit is None or used < limit, "本任务提交次数已用完；诊断与分析仍可继续", "submission_limit", "读取日志、诊断或分析已有结果")
            env = json.loads(row["environment"])
            if row["purpose"] != "environment_check":
                verified = self.store.setting("environment:" + env["fingerprint"])
                require(verified, "该启动环境尚未通过计算节点验证", "environment_unverified", "先提交 environment_check 并核验实际输出")
            db.execute("UPDATE runs SET state='pending',rules=?,updated=? WHERE id=?", (encode(policy), time.time(), run_id))
            db.execute("UPDATE tasks SET state='active' WHERE id=?", (task["id"],))
            self.store.bump(db, task["id"])
            self.store.event(db, task["id"], "submission_requested", dict(run_id=run_id, rules=policy))
            return dict(run_id=run_id, state="pending", revision=revision + 1, next_action="run_get")
        return self.store.mutate(op_id, dict(method="run_submit", run_id=run_id, revision=revision, token=token), apply)

    @public
    def run_get(self, run_id: str) -> dict:
        """Read job identity, last confirmed state, failure count and accounting independently of credentials."""
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            require(row, "找不到执行记录")
        result = dict(row)
        for field in ("environment", "rules", "evidence"):
            result[field] = json.loads(result[field])
        result["connection"] = "异常" if result["failures"] >= 2 else "读取最近证据时间"
        return result

    @public
    def run_log(self, run_id: str, op_id: str, path: str = "log.lammps", offset: int | None = None, limit: int = 65536) -> dict:
        """Read a bounded remote job log through the persistent queue; no full trajectory download.

        path is relative to the run directory. offset=None reads the current tail; an explicit byte offset reads a page.
        Returns operation_id; operation_get returns text, byte range, file size, modification time and complete thermo rows when their header is present.
        Continue from next_offset. Read physical time units from the native input. A log observation does not declare scientific completion.
        """
        require(not Path(path).is_absolute() and ".." not in Path(path).parts and path, "日志路径应位于原作业目录中")
        require((offset is None or offset >= 0) and 1 <= limit <= 262144, "日志读取范围无效")
        def apply(db):
            run = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            require(run, "找不到执行记录")
            return self.store.queue(db, run["task_id"], "log_read", dict(run_id=run_id, path=path, offset=offset, limit=limit))
        return self.store.mutate(op_id, dict(method="run_log", run_id=run_id, path=path, offset=offset, limit=limit), apply)

    @public
    def run_action(self, run_id: str, action: str, revision: int, op_id: str) -> dict:
        """Queue reconcile, collect or cancel for one run. These actions do not consume submission attempts."""
        require(action in ("reconcile", "collect", "cancel", "retry_staging"), "执行动作无效")
        def apply(db):
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            require(row, "找不到执行记录")
            task = self.store.task(db, row["task_id"])
            require(task["revision"] == revision, "任务已更新", "revision_conflict", "重新读取任务")
            if action == "retry_staging":
                require(row["state"] == "staging_failed", "只有未派发的上传失败可以重试上传")
                db.execute("UPDATE runs SET state='prepared',updated=? WHERE id=?", (time.time(), run_id))
                self.store.bump(db, row["task_id"])
                self.store.event(db, row["task_id"], "staging_retry_prepared", dict(run_id=run_id, previous_evidence=json.loads(row["evidence"])))
                return dict(run_id=run_id, state="prepared", revision=revision + 1, next_action="run_submit")
            if action == "cancel":
                if row["state"] in ("prepared", "pending", "staging_failed"):
                    db.execute("UPDATE runs SET state='cancelled_before_dispatch',updated=? WHERE id=?", (time.time(), run_id))
                else:
                    evidence = json.loads(row["evidence"])
                    evidence["cancel_requested"] = time.time()
                    db.execute("UPDATE runs SET evidence=? WHERE id=?", (encode(evidence), run_id))
                self.store.event(db, row["task_id"], "run_cancel_requested", dict(run_id=run_id))
            self.store.bump(db, row["task_id"])
            return self.store.queue(db, row["task_id"], action, dict(run_id=run_id))
        return self.store.mutate(op_id, dict(method="run_action", run_id=run_id, action=action, revision=revision), apply)

    @public
    def analysis_submit(self, task_id: str, revision: int, script: str, inputs: list[str], outputs: list[str], op_id: str, token: str | None = None) -> dict:
        """Run a native Python script against existing data in a persistent analysis queue.

        script and inputs are project-relative files. They are copied, with subdirectories preserved, into analysis/<operation_id>/.
        Python's working directory is that analysis directory, so scripts read input paths relative to its root, not their own scripts/ folder.
        outputs are required nonempty paths relative to the analysis directory. The installed analysis environment provides NumPy and Matplotlib.
        Example: script='scripts/plot.py', inputs=['data.csv'], outputs=['plot.png','statistics.csv'].
        Returns operation_id; release the task lease before polling. operation_get returns output paths, SHA-256 values and analysis.log.
        Use file_read for logs, artifact_preview for the actual image, then artifact_register to link each result to requirements.
        Physics evaluations use run_prepare/run_submit on HPC.
        """
        require(inputs and outputs and script.endswith(".py"), "需要 Python 脚本、输入与输出清单")
        for name in [script, *inputs, *outputs]:
            self.store.path(task_id, name)
        def apply(db):
            self.store.task(db, task_id, revision, token)
            self.store.bump(db, task_id)
            return self.store.queue(db, task_id, "analysis", dict(script=script, inputs=inputs, outputs=outputs))
        return self.store.mutate(op_id, dict(method="analysis_submit", task_id=task_id, revision=revision, script=script, inputs=inputs, outputs=outputs, token=token), apply)

    @public
    def artifact_register(self, task_id: str, revision: int, path: str, sources: list[str], requirement_ids: list[str], checks: list[dict], op_id: str, token: str | None = None) -> dict:
        """Link an actual output to the user's requirement IDs, source files and verification evidence.

        path, sources and checks[].evidence are project-relative. The service reads their hashes; do not calculate or repeat hashes in the request.
        Each check is {'passed': true/false, 'method': 'what was checked', 'evidence': 'path/to/check-result.json'}.
        Create the evidence file from actual analysis/inspection first. Checks must address the requirement's scientific and file-content criteria.
        The latest registration for this path supplies its current checks and complete requirement_ids. Earlier verdicts remain in history.
        A failed current check blocks its linked requirements and reopens a completed task; record corrected evidence before re-reviewing.
        An analysis result path normally starts with analysis/<operation_id>/. Returns the new task revision.
        """
        def apply(db):
            task = self.store.task(db, task_id, revision, token)
            require(set(requirement_ids) <= {r["id"] for r in task["requirements"]}, "成果要求编号不存在")
            require(checks and all(type(c.get("passed")) is bool and c.get("method") and c.get("evidence") for c in checks), "检查需要结果、方法和证据文件")
            source_map = {}
            for name in [*sources, *(c["evidence"] for c in checks)]:
                file = self.store.path(task_id, name)
                require(file.is_file(), f"来源文件缺失: {name}")
                source_map[name] = file_hash(file)
            file = self.store.path(task_id, path)
            require(file.is_file() and file.stat().st_size > 0, "成果文件为空或缺失")
            aid = identifier("artifact")
            db.execute("INSERT INTO artifacts(id,task_id,path,sha256,sources,requirements,checks,created) VALUES(?,?,?,?,?,?,?,?)", (aid, task_id, path, file_hash(file), encode(source_map), encode(requirement_ids), encode(checks), time.time()))
            self.store.bump(db, task_id)
            self.store.event(db, task_id, "artifact_registered", dict(artifact_id=aid, path=path))
            if requirement_ids and not all(check["passed"] for check in checks):
                db.execute("UPDATE tasks SET state='active' WHERE id=? AND state='completed'", (task_id,))
                self.store.event(db, task_id, "artifact_review_failed", dict(artifact_id=aid, path=path, requirements=requirement_ids))
            return dict(artifact_id=aid, path=str(file), revision=revision + 1)
        return self.store.mutate(op_id, dict(method="artifact_register", task_id=task_id, revision=revision, path=path, sources=sources, requirement_ids=requirement_ids, checks=checks, token=token), apply)

    @public
    def completion_check(self, task_id: str, revision: int, op_id: str, token: str | None = None) -> dict:
        """Check original deliverables against non-stale files and recorded scientific checks. Missing outputs keep the task open."""
        def apply(db):
            task = self.store.task(db, task_id, revision, token)
            current = manifest(self.store, task_id)
            invalidate(self.store, db, task_id, current)
            satisfied, failed_reviews = set(), set()
            archive_ids = {item["id"] for item in task["requirements"] if item.get("kind") == "archive"}
            latest = {row["path"]: row for row in db.execute("SELECT * FROM artifacts WHERE task_id=? ORDER BY created,rowid", (task_id,))}
            for row in latest.values():
                if row["stale"]:
                    continue
                if all(check["passed"] for check in json.loads(row["checks"])):
                    satisfied.update(set(json.loads(row["requirements"])) - archive_ids)
                else:
                    failed_reviews.update(json.loads(row["requirements"]))
            for row in db.execute("SELECT value FROM settings WHERE key LIKE ?", ("delivery:" + task_id + ":%",)):
                delivery = json.loads(row[0])
                archive = self.store.home / "exports" / delivery["filename"]
                if delivery["manifest_digest"] == digest(encode(current).encode()) and archive.is_file() and file_hash(archive) == delivery["sha256"]:
                    satisfied.update(set(delivery["requirement_ids"]) & archive_ids)
            missing = [item for item in task["requirements"] if item["id"] not in satisfied or item["id"] in failed_reviews]
            active = db.execute("SELECT count(*) FROM runs WHERE task_id=? AND state IN ('pending','dispatching','unknown','queued','running','cancelling')", (task_id,)).fetchone()[0]
            active_work = db.execute("SELECT count(*) FROM queue WHERE task_id=? AND state IN ('pending','running') AND kind!='decision'", (task_id,)).fetchone()[0]
            complete = not missing and not active and not active_work
            db.execute("UPDATE tasks SET state=? WHERE id=?", ("completed" if complete else task["state"] if task["state"] != "completed" else "active", task_id))
            self.store.bump(db, task_id)
            result = dict(complete=complete, missing=missing, failed_reviews=sorted(failed_reviews), active_runs=active, active_work=active_work, revision=revision + 1)
            self.store.event(db, task_id, "research_completed" if complete else "deliverables_missing", result)
            return result
        return self.store.mutate(op_id, dict(method="completion_check", task_id=task_id, revision=revision, token=token), apply)

    @public
    def project_export(self, task_id: str, revision: int, op_id: str, token: str | None = None, requirement_ids: list[str] | None = None) -> dict:
        """Export project files, original request, required results, history, rules, job accounting and continuation instructions to ZIP.

        Returns operation_id immediately; release the lease before waiting. operation_get returns the archive's absolute path and hash.
        Another task can pass that ZIP to project_import. The archive is outside project_path, so do not register its absolute path as a project file.
        By default this satisfies all kind='archive' requirements after verifying the real ZIP. Run completion_check after export completes.
        """
        def apply(db):
            task = self.store.task(db, task_id, revision, token)
            archive_ids = {item["id"] for item in task["requirements"] if item.get("kind") == "archive"}
            selected = sorted(archive_ids) if requirement_ids is None else requirement_ids
            require(set(selected) <= archive_ids, "导出成果编号必须指向 archive 类型要求")
            return self.store.queue(db, task_id, "export", dict(requirement_ids=selected))
        return self.store.mutate(op_id, dict(method="project_export", task_id=task_id, revision=revision, token=token, requirement_ids=requirement_ids), apply)

    @public
    def units_convert(self, value: float, unit: str, target: str) -> dict:
        """Convert dimensionally compatible scientific units, retaining the original expression (including Å³)."""
        from .science import convert
        return convert(value, unit, target)

    @public
    def data_inspect(self, task_id: str, path: str, kind: str) -> dict:
        """Inspect actual LAMMPS thermo or per-atom trajectory data. Columns, frame counts and identities come from files."""
        from .science import thermo_blocks, inspect_dump
        file = self.store.path(task_id, path)
        require(file.is_file(), "数据文件缺失")
        require(kind in ("thermo", "trajectory"), "kind 应为 thermo 或 trajectory")
        if kind == "thermo":
            blocks = thermo_blocks(file)
            require(blocks and all(b["rows"] for b in blocks), "未找到完整温度等热力学输出")
            result = dict(blocks=[dict(columns=b["columns"], rows=len(b["rows"]), first=b["rows"][0], last=b["rows"][-1], first_line=b["first_line"]) for b in blocks])
        else:
            result = inspect_dump(file)
        return dict(path=path, sha256=file_hash(file), kind=kind, **result)

    @public
    def resource_register(self, name: str, kind: str, location: str, provenance: dict, op_id: str) -> dict:
        """Register an existing potential, structure, author project, publication or environment with provenance and license."""
        require(kind in ("potential", "structure", "author_project", "publication", "environment", "results"), "资源类型无效")
        require(name and location and provenance.get("source") and provenance.get("license"), "资源需要名称、位置、来源和许可")
        if kind == "publication":
            require(provenance.get("title") and provenance.get("doi"), "论文需要完整标题和 DOI")
        value = dict(name=name, kind=kind, location=location, provenance=provenance, registered_at=time.time())
        local = Path(location).expanduser()
        if local.is_file():
            value["sha256"] = file_hash(local)
        def apply(db):
            key = identifier("resource")
            self.store.set_setting(db, key, value)
            self.store.event(db, None, "resource_registered", dict(id=key, name=name))
            return dict(id=key, **value)
        return self.store.mutate(op_id, dict(method="resource_register", name=name, kind=kind, location=location, provenance=provenance), apply)

    @public
    def resource_search(self, query: str = "", kind: str | None = None) -> dict:
        """Search the reusable local resource catalog before retrieving or rebuilding assets."""
        with self.store.connect() as db:
            rows = db.execute("SELECT * FROM settings WHERE key LIKE 'resource_%'").fetchall()
        results = []
        for row in rows:
            value = json.loads(row["value"])
            if query.lower() in encode(value).lower() and (kind is None or value["kind"] == kind):
                results.append(dict(id=row["key"], **value))
        return dict(resources=results)

    @public
    def background_request(self, task_id: str, revision: int, instruction: str, op_id: str, expected_outputs: list[str] | None = None) -> dict:
        """Persist a concrete continuation request for background Codex. automation reports whether the project and shared service allow dispatch; a held request remains saved until task_background enables it."""
        require(instruction.strip(), "需要接续任务说明")
        for path in expected_outputs or []:
            self.store.path(task_id, path)
        def apply(db):
            self.store.task(db, task_id, revision)
            self.store.bump(db, task_id)
            return dict(**self.store.queue(db, task_id, "decision", dict(events=[dict(reason="user_request", instruction=instruction)], expected_outputs=expected_outputs or [])), automation=self.background_status(db, task_id))
        return self.store.mutate(op_id, dict(method="background_request", task_id=task_id, revision=revision, instruction=instruction, expected_outputs=expected_outputs), apply)

    @public
    def legacy_inspect(self, database: str) -> dict:
        """Inspect a historical SQLite catalog through a read-only connection; never initialize its old controller."""
        import sqlite3
        path = Path(database).expanduser().resolve()
        require(path.is_file(), "历史数据库不存在")
        before = file_hash(path)
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            db.execute("PRAGMA query_only=ON")
            names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            tables = []
            for name in names:
                quoted = '"' + name.replace('"', '""') + '"'
                columns = [r[1] for r in db.execute("PRAGMA table_info(" + quoted + ")")]
                count = db.execute("SELECT count(*) FROM " + quoted).fetchone()[0]
                tables.append(dict(name=name, columns=columns, rows=count))
        finally:
            db.close()
        return dict(tables=tables, database_sha256_before=before, database_sha256_after=file_hash(path), access="read_only")

    @public
    def workflow_register(self, task_id: str, revision: int, stages: list[dict], op_id: str, token: str | None = None) -> dict:
        """Register an ordered native-script workflow. Each stage has id, kind (run/analysis), after IDs and spec. Run spec has run_id; analysis spec has script, inputs, outputs."""
        require(stages, "阶段清单不能为空")
        def apply(db):
            self.store.task(db, task_id, revision, token)
            known = {row[0] for row in db.execute("SELECT id FROM stages WHERE task_id=?", (task_id,))}
            for stage in stages:
                sid, kind, spec = stage.get("id"), stage.get("kind"), stage.get("spec", {})
                require(sid and sid not in known and kind in ("run", "analysis"), "阶段编号必须唯一，类型为 run 或 analysis")
                require(set(stage.get("after", [])) <= known, "依赖阶段必须在前面登记，且不能循环")
                if kind == "run":
                    row = db.execute("SELECT id FROM runs WHERE id=? AND task_id=?", (spec.get("run_id"), task_id)).fetchone()
                    require(row, "执行阶段需要同一任务的准备记录")
                else:
                    require(set(spec) == {"script", "inputs", "outputs"} and spec["inputs"] and spec["outputs"], "分析阶段需要脚本、输入和预期输出")
                    for path in [spec["script"], *spec["inputs"], *spec["outputs"]]:
                        self.store.path(task_id, path)
                db.execute("INSERT INTO stages(task_id,id,kind,dependencies,specification,created,updated) VALUES(?,?,?,?,?,?,?)", (task_id, sid, kind, encode(stage.get("after", [])), encode(spec), time.time(), time.time()))
                known.add(sid)
            self.store.bump(db, task_id)
            self.store.event(db, task_id, "workflow_registered", dict(stages=stages))
            return dict(task_id=task_id, stage_ids=[s["id"] for s in stages], revision=revision + 1)
        return self.store.mutate(op_id, dict(method="workflow_register", task_id=task_id, revision=revision, stages=stages, token=token), apply)

