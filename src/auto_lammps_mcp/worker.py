"""Persistent background work, cluster polling and Codex continuation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

from .core import Fault, digest, encode, identifier, require
from .files import atomic_write, file_hash, import_directory, invalidate, manifest
from .slurm import ACTIVE, TERMINAL, Slurm


class Worker:
    def __init__(self, service, slurm=None):
        self.service, self.store = service, service.store
        self.slurm = slurm or Slurm(self.store)
        self.stopping = threading.Event()
        self.last_poll = 0
        self.last_summary = 0
        self.boot_id = identifier("service")
        self.started_at = time.time()

    def loop(self):
        self.recover_processes()
        # A previous process cannot retain execution ownership after a daemon restart.
        with self.store.transaction() as db:
            self.store.event(db, None, "service_started", dict(boot_id=self.boot_id, pid=os.getpid(), started_at=self.started_at))
            db.execute("UPDATE runs SET state='unknown' WHERE state='dispatching'")
            db.execute("UPDATE queue SET state='pending',token=NULL,lease_until=NULL WHERE state='running' AND kind IN ('snapshot','import','export','collect','reconcile','cancel','connection_check','log_read')")
            rows = db.execute("SELECT id,task_id,kind FROM queue WHERE state='running'").fetchall()
            for row in rows:
                db.execute("UPDATE queue SET state='interrupted',updated=? WHERE id=?", (time.time(), row["id"]))
                self.store.event(db, row["task_id"], "work_interrupted", dict(operation_id=row["id"], kind=row["kind"], next_action="核对已有产物后接续"))
            db.execute("DELETE FROM leases WHERE owner LIKE 'background:%' OR owner LIKE 'worker:%'")
        threading.Thread(target=self.monitor_loop, daemon=True, name="alm-monitor").start()
        threading.Thread(target=self.decision_loop, daemon=True, name="alm-decisions").start()
        threading.Thread(target=self.dispatch_loop, daemon=True, name="alm-dispatch").start()
        while not self.stopping.is_set():
            try:
                if self.store.setting("recovery", {}).get("pending"):
                    self.stopping.wait(1)
                    continue
                self.advance_stages()
                self.work_one()
            except Exception as exc:
                with self.store.transaction() as db:
                    self.store.event(db, None, "worker_error", dict(type=type(exc).__name__, message=str(exc)))
            self.stopping.wait(1)

    def recover_processes(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT key,value FROM settings WHERE key LIKE 'process:%'").fetchall()
        for row in rows:
            record = json.loads(row["value"])
            observed = subprocess.run(["ps", "-p", str(record["pid"]), "-o", "lstart="], capture_output=True, text=True)
            if observed.stdout.strip() and observed.stdout.strip() == record["started"]:
                try:
                    os.killpg(record["pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass
            with self.store.transaction() as db:
                db.execute("DELETE FROM settings WHERE key=?", (row["key"],))
                self.store.event(db, record.get("task_id"), "interrupted_process_reconciled", dict(operation_id=record["operation_id"]))

    def process(self, work, command, *, cwd=None, env=None, output, timeout):
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        started = subprocess.run(["ps", "-p", str(process.pid), "-o", "lstart="], capture_output=True, text=True).stdout.strip()
        key = "process:" + work["id"]
        with self.store.transaction() as db:
            self.store.set_setting(db, key, dict(pid=process.pid, started=started, operation_id=work["id"], task_id=work["task_id"]))
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    return process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise Fault("execution_timeout", "此轮后台处理超过配置时限", "核对原操作输出后接续", "partial")
                    with self.store.connect() as db:
                        task = self.store.task(db, work["task_id"])
                        if work.get("owns_lease"):
                            self.store.task(db, work["task_id"], token=work["token"])
                        if work["kind"] == "decision" and task["state"] in ("paused", "stopping", "stopped"):
                            raise Fault("task_paused", "后台接续已按用户要求停止", "读取当前任务状态", "partial")
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        finally:
            with self.store.transaction() as db:
                db.execute("DELETE FROM settings WHERE key=?", (key,))

    def advance_stages(self):
        with self.store.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT s.* FROM stages s JOIN tasks t ON t.id=s.task_id WHERE t.state='active' AND s.state IN ('pending','running') ORDER BY s.created")]
        for stage in rows:
            task_id, sid = stage["task_id"], stage["id"]
            spec = json.loads(stage["specification"])
            with self.store.transaction() as db:
                task = self.store.task(db, task_id)
                lease = db.execute("SELECT 1 FROM leases WHERE task_id=? AND expires>?", (task_id, time.time())).fetchone()
                if task["state"] != "active" or lease:
                    continue
                dependencies = json.loads(stage["dependencies"])
                states = {r["id"]: r["state"] for r in db.execute("SELECT id,state FROM stages WHERE task_id=?", (task_id,))}
                if any(states.get(key) != "completed" for key in dependencies):
                    continue
                if stage["state"] == "running":
                    state, result = None, None
                    if stage["kind"] == "run":
                        run = db.execute("SELECT * FROM runs WHERE id=?", (spec["run_id"],)).fetchone()
                        collected = db.execute("SELECT * FROM queue WHERE kind='collect' AND task_id=? AND state='completed' AND instr(payload,?)>0", (task_id, spec["run_id"])).fetchone()
                        if run["state"] in TERMINAL and collected:
                            state = "completed" if run["state"] == "completed" and json.loads(run["evidence"]).get("exit_code") == "0:0" else "failed"
                            result = dict(run_id=run["id"], collection=json.loads(collected["result"]))
                    else:
                        work = db.execute("SELECT state,result FROM queue WHERE id=?", (stage["operation_id"],)).fetchone()
                        if work and work["state"] in ("completed", "failed", "interrupted"):
                            state, result = work["state"], json.loads(work["result"]) if work["result"] else None
                    if state:
                        db.execute("UPDATE stages SET state=?,result=?,updated=? WHERE task_id=? AND id=?", (state, encode(result), time.time(), task_id, sid))
                        self.store.event(db, task_id, "stage_" + state, dict(stage_id=sid, result=result))
                    continue
                if stage["kind"] == "analysis":
                    operation = self.store.queue(db, task_id, "analysis", spec)
                    db.execute("UPDATE stages SET state='running',operation_id=?,updated=? WHERE task_id=? AND id=?", (operation["operation_id"], time.time(), task_id, sid))
                    self.store.event(db, task_id, "stage_started", dict(stage_id=sid, **operation))
                    continue
            try:
                # run_submit takes its own transaction and checks state and revision again.
                result = self.service.run_submit(spec["run_id"], task["revision"], "stage-submit:" + task_id + ":" + sid)
                state = "running"
            except Fault as exc:
                if exc.code in ("lease_conflict", "revision_conflict"):
                    continue
                result, state = dict(error=exc.as_dict()), "failed"
            with self.store.transaction() as db:
                db.execute("UPDATE stages SET state=?,result=?,updated=? WHERE task_id=? AND id=?", (state, encode(result), time.time(), task_id, sid))
                self.store.event(db, task_id, "stage_" + state, dict(stage_id=sid, result=result))

    def monitor_loop(self):
        while not self.stopping.is_set():
            try:
                if self.store.setting("recovery", {}).get("pending"):
                    self.stopping.wait(1)
                    continue
                self.tick()
            except Exception as exc:
                with self.store.transaction() as db:
                    self.store.event(db, None, "monitor_error", dict(type=type(exc).__name__, message=str(exc)))
            self.stopping.wait(1)

    def tick(self):
        now = time.time()
        if now - self.last_poll >= 60:
            self.poll()
            self.last_poll = now
            with self.store.transaction() as db:
                heartbeat = dict(boot_id=self.boot_id, pid=os.getpid(), started_at=self.started_at, observed_at=time.time(),
                                 python=sys.executable)
                self.store.set_setting(db, "service_heartbeat", heartbeat)
                self.store.event(db, None, "service_heartbeat", heartbeat)
        self.notifications()
        if now - self.last_summary >= 300:
            with self.store.transaction() as db:
                for task in db.execute("SELECT id,title,state FROM tasks WHERE state IN ('active','paused','stopping')").fetchall():
                    jobs = [dict(row) for row in db.execute("SELECT id,state,updated FROM runs WHERE task_id=?", (task["id"],))]
                    self.store.event(db, task["id"], "progress_summary", dict(title=task["title"], state=task["state"], jobs=jobs))
            self.last_summary = now

    def dispatch_loop(self):
        while not self.stopping.is_set():
            try:
                if not self.store.setting("recovery", {}).get("pending"):
                    self.dispatch_one()
            except Exception as exc:
                with self.store.transaction() as db:
                    self.store.event(db, None, "dispatch_worker_error", dict(type=type(exc).__name__, message=str(exc)))
            self.stopping.wait(1)

    def preparation_progress(self, run, started, phase, event):
        with self.store.transaction() as db:
            row = db.execute("SELECT evidence FROM runs WHERE id=?", (run["id"],)).fetchone()
            evidence = json.loads(row[0])
            progress = dict(phase=phase, started_at=started, observed_at=time.time())
            evidence["preparation"] = progress
            db.execute("UPDATE runs SET evidence=?,updated=? WHERE id=?", (encode(evidence), time.time(), run["id"]))
            self.store.event(db, run["task_id"], event, dict(run_id=run["id"], **progress))

    def preparation_heartbeat(self, run, started, finished):
        while not finished.wait(30):
            self.preparation_progress(run, started, "uploading", "staging_progress")

    def dispatch_one(self):
        with self.store.transaction() as db:
            row = db.execute("SELECT r.* FROM runs r JOIN tasks t ON t.id=r.task_id WHERE r.state='pending' AND t.state='active' ORDER BY r.created LIMIT 1").fetchone()
            if not row:
                return
            run = dict(row)
            active = db.execute("SELECT count(*) FROM runs WHERE cluster=? AND state IN ('dispatching','unknown','queued','running','cancelling')", (run["cluster"],)).fetchone()[0]
            if active:
                return
            # Staging contains no sbatch. It can be repeated after interruption.
        started, finished = time.time(), threading.Event()
        self.preparation_progress(run, started, "uploading", "staging_started")
        heartbeat = threading.Thread(target=self.preparation_heartbeat, args=(run, started, finished), daemon=True)
        heartbeat.start()
        try:
            self.slurm.stage(run)
        except Exception as exc:
            with self.store.transaction() as db:
                db.execute("UPDATE runs SET state='staging_failed',evidence=?,updated=? WHERE id=? AND state='pending'", (encode(dict(error=str(exc))), time.time(), run["id"]))
                self.store.event(db, run["task_id"], "staging_failed", dict(run_id=run["id"], error=str(exc)))
            self.decision(run["task_id"], "staging_failed", dict(run_id=run["id"], error=str(exc)))
            return
        finally:
            finished.set()
        self.preparation_progress(run, started, "uploaded", "staging_finished")
        with self.store.transaction() as db:
            task = self.store.task(db, run["task_id"])
            latest = db.execute("SELECT state,rules FROM runs WHERE id=?", (run["id"],)).fetchone()
            if self.stopping.is_set() or task["state"] != "active" or latest["state"] != "pending":
                return
            if latest["rules"] != run["rules"]:
                self.store.event(db, run["task_id"], "staging_rules_changed", dict(run_id=run["id"], next_action="按最新规则重新准备输入"))
                return
            db.execute("UPDATE runs SET state='dispatching',updated=? WHERE id=?", (time.time(), run["id"]))
            self.store.event(db, run["task_id"], "dispatch_started", dict(run_id=run["id"]))
        try:
            job_id = self.slurm.submit(run)
        except Exception as exc:
            with self.store.transaction() as db:
                db.execute("UPDATE runs SET state='unknown',evidence=?,updated=? WHERE id=?", (encode(dict(error=str(exc))), time.time(), run["id"]))
                self.store.event(db, run["task_id"], "submission_unknown", dict(run_id=run["id"], error=str(exc), next_action="reconcile"))
            return
        with self.store.transaction() as db:
            db.execute("UPDATE runs SET job_id=?,state='queued',updated=? WHERE id=?", (job_id, time.time(), run["id"]))
            self.store.event(db, run["task_id"], "job_accepted", dict(run_id=run["id"], job_id=job_id))
            task = self.store.task(db, run["task_id"])
            latest = db.execute("SELECT evidence FROM runs WHERE id=?", (run["id"],)).fetchone()[0]
            if task["state"] == "stopping" or json.loads(latest).get("cancel_requested"):
                self.store.queue(db, run["task_id"], "cancel", dict(run_id=run["id"]))

    def poll(self):
        with self.store.connect() as db:
            runs = [dict(row) for row in db.execute("SELECT * FROM runs WHERE state IN ('dispatching','unknown','queued','running','cancelling')")]
        for run in runs:
            if run["state"] == "unknown" and not run["job_id"]:
                try:
                    found = self.slurm.reconcile(run)
                    if found:
                        with self.store.transaction() as db:
                            db.execute("UPDATE runs SET job_id=?,state='queued',failures=0,updated=? WHERE id=?", (found, time.time(), run["id"]))
                            self.store.event(db, run["task_id"], "job_reconciled", dict(run_id=run["id"], job_id=found))
                        run["job_id"], run["state"] = found, "queued"
                except Exception as exc:
                    self.query_failure(run, exc)
        for cluster in sorted({r["cluster"] for r in runs if r["job_id"]}):
            group = [r for r in runs if r["cluster"] == cluster and r["job_id"]]
            try:
                observed = self.slurm.query(cluster, group)
            except Exception as exc:
                for run in group:
                    self.query_failure(run, exc)
                continue
            for run in group:
                evidence = observed.get(run["job_id"])
                if not evidence:
                    self.query_failure(run, "调度器暂未返回该作业；保留最后确认状态")
                    continue
                state = evidence["state"]
                with self.store.transaction() as db:
                    latest = db.execute("SELECT evidence FROM runs WHERE id=?", (run["id"],)).fetchone()[0]
                    evidence = {**json.loads(latest), **evidence, "confirmed_at": time.time()}
                    db.execute("UPDATE runs SET state=?,evidence=?,failures=0,updated=? WHERE id=?", (state, encode(evidence), time.time(), run["id"]))
                    if state != run["state"]:
                        self.store.event(db, run["task_id"], "job_state_changed", dict(run_id=run["id"], **evidence))
                    if state in TERMINAL and run["state"] not in TERMINAL:
                        self.store.queue(db, run["task_id"], "collect", dict(run_id=run["id"]))
                    task = self.store.task(db, run["task_id"])
                    if state not in TERMINAL and (task["state"] == "stopping" or evidence.get("cancel_requested")):
                        existing = db.execute("SELECT 1 FROM queue WHERE task_id=? AND kind='cancel' AND state IN ('pending','running')", (run["task_id"],)).fetchone()
                        if not existing:
                            self.store.queue(db, run["task_id"], "cancel", dict(run_id=run["id"]))
        with self.store.transaction() as db:
            for task in db.execute("SELECT id FROM tasks WHERE state='stopping'").fetchall():
                active = db.execute("SELECT 1 FROM runs WHERE task_id=? AND state IN ('pending','dispatching','unknown','queued','running','cancelling')", (task["id"],)).fetchone()
                if not active:
                    db.execute("UPDATE tasks SET state='stopped' WHERE id=?", (task["id"],))
                    self.store.event(db, task["id"], "task_stopped", {})

    def query_failure(self, run, error):
        with self.store.transaction() as db:
            db.execute("UPDATE runs SET failures=failures+1 WHERE id=?", (run["id"],))
            count = db.execute("SELECT failures FROM runs WHERE id=?", (run["id"],)).fetchone()[0]
            if count == 2:
                self.store.event(db, run["task_id"], "connection_lost", dict(run_id=run["id"], error=str(error), last_confirmed=run["updated"]))

    def decision_loop(self):
        # The agent may await queued imports, analysis or exports during one turn.
        # Keep its consumer independent from those concrete operations.
        while not self.stopping.is_set():
            try:
                if not self.store.setting("recovery", {}).get("pending"):
                    self.work_one(decisions=True)
            except Exception as exc:
                with self.store.transaction() as db:
                    self.store.event(db, None, "decision_worker_error", dict(type=type(exc).__name__, message=str(exc)))
            self.stopping.wait(1)

    def work_one(self, decisions=False):
        with self.store.transaction() as db:
            selector = "kind='decision'" if decisions else "kind!='decision'"
            rows = db.execute("SELECT * FROM queue WHERE state='pending' AND " + selector + " ORDER BY created").fetchall()
            work = None
            for row in rows:
                candidate = dict(row)
                if candidate["task_id"]:
                    lease = db.execute("SELECT * FROM leases WHERE task_id=? AND expires>?", (candidate["task_id"], time.time())).fetchone()
                    if lease:
                        continue
                    task = self.store.task(db, candidate["task_id"])
                    if candidate["kind"] == "decision" and (task["state"] in ("paused", "stopping", "stopped") or not self.store.setting("background", {}).get("enabled")):
                        continue
                work = candidate
                break
            if work is None:
                return
            token = identifier("worker")
            work["token"] = token
            work["owns_lease"] = work["task_id"] is not None and work["kind"] in ("snapshot", "import", "analysis", "export")
            if work["owns_lease"]:
                db.execute("INSERT OR REPLACE INTO leases VALUES(?,?,?,?)", (work["task_id"], "worker:" + work["id"], token, time.time() + 120))
            db.execute("UPDATE queue SET state='running',token=?,lease_until=?,updated=? WHERE id=?", (token, time.time() + 120, time.time(), work["id"]))
        finished = threading.Event()
        heartbeat = threading.Thread(target=self.heartbeat, args=(work, token, finished), daemon=True)
        heartbeat.start()
        try:
            result = self.execute(work)
            state = "completed"
        except Exception as exc:
            result = dict(error=exc.as_dict() if isinstance(exc, Fault) else dict(type=type(exc).__name__, message=str(exc)))
            state = "failed"
        finally:
            finished.set()
        with self.store.transaction() as db:
            if work["owns_lease"]:
                lease = db.execute("SELECT token,expires FROM leases WHERE task_id=?", (work["task_id"],)).fetchone()
                if not lease or lease["token"] != token or lease["expires"] <= time.time():
                    state = "interrupted"
                    result = dict(previous_result=result, reason="执行权已交接，保留产物供核对")
                db.execute("DELETE FROM leases WHERE task_id=? AND token=?", (work["task_id"], token))
            changed = db.execute("UPDATE queue SET state=?,result=?,lease_until=NULL,updated=? WHERE id=? AND token=?", (state, encode(result), time.time(), work["id"], token)).rowcount
            if changed:
                self.store.event(db, work["task_id"], "work_" + state, dict(operation_id=work["id"], kind=work["kind"], result=result))
        if state == "failed" and work["kind"] != "decision" and work["task_id"]:
            self.decision(work["task_id"], "work_failed", dict(operation_id=work["id"], result=result))

    def heartbeat(self, work, token, finished):
        while not finished.wait(30):
            with self.store.transaction() as db:
                if work.get("owns_lease"):
                    db.execute("UPDATE leases SET expires=? WHERE task_id=? AND token=? AND expires>?", (time.time() + 120, work["task_id"], token, time.time()))
                db.execute("UPDATE queue SET lease_until=?,updated=? WHERE id=? AND token=? AND state='running'", (time.time() + 120, time.time(), work["id"], token))
                self.store.event(db, work["task_id"], "work_progress", dict(operation_id=work["id"], kind=work["kind"]))

    def execute(self, work):
        from .records import restored_paths
        payload = restored_paths(self.store, json.loads(work["payload"]))
        kind, task_id = work["kind"], work["task_id"]
        if kind == "backup":
            from .backup import create_backup
            return create_backup(self.store, payload["destination"])
        if kind == "snapshot":
            files = manifest(self.store, task_id, payload.get("paths"))
            sid = identifier("snapshot")
            with self.store.transaction() as db:
                current = self.store.task(db, task_id, token=work["token"])
                if payload.get("paths") is None:
                    invalidate(self.store, db, task_id, files)
                db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?)", (sid, task_id, encode(files), digest(encode(files).encode()), current["revision"], time.time()))
                self.store.event(db, task_id, "snapshot_created", dict(snapshot_id=sid, files=len(files)))
            return dict(snapshot_id=sid, files=files)
        if kind == "import":
            from .handoff import import_project
            result = import_project(self.store, task_id, payload["source"])
            with self.store.transaction() as db:
                self.store.task(db, task_id, token=work["token"])
                self.store.bump(db, task_id)
            return result
        if kind == "connection_check":
            result = self.slurm.connection_check(self.store.setting("cluster"))
            result["confirmed_at"] = time.time()
            with self.store.transaction() as db:
                self.store.set_setting(db, "cluster_receipt", result)
            return result
        if kind == "log_read":
            with self.store.connect() as db:
                run = dict(db.execute("SELECT * FROM runs WHERE id=?", (payload["run_id"],)).fetchone())
            return self.slurm.read_log(run, payload["path"], payload.get("offset"), payload.get("limit", 65536))
        if kind in ("collect", "reconcile", "cancel"):
            with self.store.connect() as db:
                if payload.get("run_id"):
                    runs = [dict(db.execute("SELECT * FROM runs WHERE id=?", (payload["run_id"],)).fetchone())]
                else:
                    runs = [dict(row) for row in db.execute("SELECT * FROM runs WHERE task_id=? AND state IN ('dispatching','unknown','queued','running','cancelling')", (task_id,))]
            results = []
            for run in runs:
                if kind == "collect":
                    result = self.slurm.collect(run)
                    self.decision(task_id, "results_collected", dict(run_id=run["id"], **result))
                elif kind == "reconcile":
                    job_id = self.slurm.reconcile(run)
                    result = dict(job_id=job_id, state="found" if job_id else "unknown")
                    if job_id:
                        with self.store.transaction() as db:
                            db.execute("UPDATE runs SET job_id=?,state='queued',updated=? WHERE id=?", (job_id, time.time(), run["id"]))
                else:
                    self.slurm.cancel(run)
                    result = dict(cancel_sent=bool(run["job_id"]), run_id=run["id"])
                    with self.store.transaction() as db:
                        if run["job_id"] and run["state"] not in TERMINAL:
                            db.execute("UPDATE runs SET state='cancelling' WHERE id=?", (run["id"],))
                results.append(result)
            return dict(results=results)
        if kind == "analysis":
            return self.analysis(work, payload)
        if kind == "decision":
            return self.background(work, payload)
        if kind == "export":
            return self.export(task_id, payload.get("requirement_ids", []), work["token"])
        raise Fault("unknown_work", kind, "检查当前版本工作类型")

    def analysis(self, work, payload):
        task_id = work["task_id"]
        source = self.store.root(task_id)
        result_root = self.store.path(task_id, "analysis/" + work["id"])
        result_root.mkdir(parents=True, exist_ok=True)
        sources = {}
        for name in [payload["script"], *payload["inputs"]]:
            original = self.store.path(task_id, name)
            require(original.is_file(), f"分析输入缺失: {name}")
            destination = result_root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
            sources[name] = file_hash(destination)
        output = result_root / "analysis.log"
        environment = {**os.environ, "MPLBACKEND": "Agg", "PYTHONUNBUFFERED": "1"}
        with output.open("wb") as log:
            returncode = self.process(work, [sys.executable, payload["script"]], cwd=result_root, env=environment, output=log, timeout=3600)
        require(returncode == 0, "分析失败，计算结果已保留", "analysis_failed", "读取分析日志并修改脚本")
        files = {}
        for name in payload["outputs"]:
            path = result_root / name
            require(path.is_file() and path.stat().st_size > 0, f"分析成果缺失: {name}", "output_missing", "读取分析日志和输出目录")
            files[name] = dict(path=str(path), sha256=file_hash(path))
        receipt = dict(sources=sources, outputs=files, script=payload["script"], log=str(output))
        atomic_write(result_root / "analysis-receipt.json", encode(receipt).encode())
        return receipt

    def decision(self, task_id, reason, evidence):
        with self.store.transaction() as db:
            self.store.event(db, task_id, "decision_needed", dict(reason=reason, evidence=evidence))
            config = self.store.setting("background", {})
            pending = db.execute("SELECT id,payload FROM queue WHERE task_id=? AND kind='decision' AND state='pending'", (task_id,)).fetchone()
            if pending:
                payload = json.loads(pending["payload"])
                payload.setdefault("events", []).append(dict(reason=reason, evidence=evidence))
                db.execute("UPDATE queue SET payload=? WHERE id=?", (encode(payload), pending["id"]))
            else:
                self.store.queue(db, task_id, "decision", dict(events=[dict(reason=reason, evidence=evidence)]))

    def background(self, work, payload):
        from .installation import codex_path
        from .network import background_environment
        config = self.store.setting("background", {})
        require(config.get("enabled"), "后台 Codex 尚未启用，待判断事项已保存", "background_disabled", "配置后台或在前台接续此任务")
        codex = config.get("codex") or codex_path()
        require(codex, "未找到后台 Codex", "model_unavailable", "检查 Codex 安装与登录")
        task_id = work["task_id"]
        root = self.store.root(task_id)
        output_dir = self.store.home / "logs" / work["id"]
        output_dir.mkdir(exist_ok=True)
        prompt = ("继续 Auto-LAMMPS 科研任务。读取 auto-lammps-mcp skill。任务编号: " + task_id +
                  "。先用 task_get 读取原始要求、当前规则和证据。通过 MCP 或 alm CLI 完成诊断、原生文件修改、分析和交接。"
                  "所有物理求值经已记账的 run_prepare/run_submit；按任务授权和固定条件执行。"
                  "变更前取得 task_claim，结束时释放。用户已暂停或停止则只汇报事实。"
                  "不得把调度结束作为研究完成；检查每项成果。若缺少用户决定，保存具体问题和已完成工作。"
                  "当前待办：" + encode(payload))
        binary = str(Path(sys.executable).parent / "alm")
        connection_args = ["--home", str(self.store.home), "mcp", "--host", "background-codex"]
        command = [codex, "exec", "--json", "--cd", str(root), "--skip-git-repo-check", "--approve-for-me",
                   "-c", "mcp_servers.auto-lammps.command=" + json.dumps(binary),
                   "-c", "mcp_servers.auto-lammps.args=" + json.dumps(connection_args),
                   "--output-last-message", str(output_dir / "summary.txt")]
        if config.get("profile"):
            command.extend(["--profile", config["profile"]])
        command.append(prompt)
        with (output_dir / "events.jsonl").open("wb") as output:
            returncode = self.process(work, command, output=output, timeout=config.get("timeout_seconds", 900), env={**background_environment(), "ALM_HOME": str(self.store.home)})
        require(returncode == 0, "后台 Codex 未完成此轮处理", "model_unavailable", "读取后台日志，恢复登录或从前台接续")
        summary = (output_dir / "summary.txt").read_text() if (output_dir / "summary.txt").exists() else ""
        require(summary.strip(), "后台回复为空", "model_truncated", "保留原待办，读取日志后接续")
        calls = self.verify_background_output(task_id, output_dir / "events.jsonl", payload.get("expected_outputs", []))
        receipt = dict(work_id=work["id"], completed_at=time.time(), summary=summary, verified=True, evidence=calls)
        with self.store.transaction() as db:
            self.store.set_setting(db, "background_receipt", receipt)
        return receipt

    def verify_background_output(self, task_id, log, expected_outputs):
        successful = []
        for line in log.read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get("item", {})
            if event.get("type") == "item.completed" and item.get("type") == "mcp_tool_call" and item.get("status") == "completed" and not item.get("error"):
                result = item.get("result") or {}
                if not result.get("isError"):
                    successful.append(dict(tool=item.get("tool"), arguments=item.get("arguments", {})))
        require(any(c["tool"] == "task_get" and c["arguments"].get("task_id") == task_id for c in successful),
                "后台未能读取本任务的实际工具回执", "background_unverified", "读取后台调用错误并恢复工具接入")
        outputs = {}
        for name in expected_outputs:
            path = self.store.path(task_id, name)
            require(path.is_file() and path.stat().st_size > 0, f"后台成果缺失: {name}", "output_missing", "保留此次记录，接续缺少的成果")
            outputs[name] = dict(sha256=file_hash(path), bytes=path.stat().st_size)
        return dict(task_read=True, tools=[c["tool"] for c in successful], outputs=outputs)

    def export(self, task_id, requirement_ids=None, token=None):
        task = self.service.task_get(task_id, include_details=True)
        files = manifest(self.store, task_id)
        key = identifier("export")
        archive = self.store.home / "exports" / (key + ".zip")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for name, info in files.items():
                package.write(self.store.home / "blobs" / info["sha256"], "project/" + name)
            package.writestr("project-manifest.json", encode(files))
            package.writestr("research-record.json", encode(task))
            with self.store.connect() as db:
                events = [dict(row) for row in db.execute("SELECT * FROM events WHERE task_id=? ORDER BY seq", (task_id,))]
            package.writestr("events.json", encode(events))
            package.writestr("README.txt", "Auto-LAMMPS 研究交接\n打开 research-record.json 读取原始需求、规则、执行与成果。\nproject/ 保存原生脚本、输入、数据和图表。project-manifest.json 保存 SHA-256。\n在新对话中说：使用 Auto-LAMMPS 导入这个研究结果包，说明已完成内容和剩余成果，然后继续分析。\n")
        receipt = dict(path=str(archive), filename=archive.name, sha256=file_hash(archive), files=len(files),
                       manifest_digest=digest(encode(files).encode()), requirement_ids=requirement_ids or [])
        with self.store.transaction() as db:
            self.store.task(db, task_id, token=token)
            self.store.set_setting(db, "delivery:" + task_id + ":" + key, receipt)
            self.store.event(db, task_id, "project_exported", receipt)
        return receipt

    def notifications(self):
        config = self.store.setting("notifications", {})
        if not config.get("enabled") or not config.get("desktop") or sys.platform != "darwin":
            return
        cursor = self.store.setting("notification_cursor", 0)
        events = self.service.events(after=cursor)
        meaningful = {"job_accepted", "job_state_changed", "connection_lost", "decision_needed", "research_completed", "task_stopped", "work_failed"}
        relevant = [e for e in events["events"] if e["kind"] in meaningful]
        if relevant:
            message = f"{len(relevant)} 条新进展。回到对话后输入：查看 Auto-LAMMPS 未读进展。"
            script = 'on run argv\ndisplay notification (item 1 of argv) with title "Auto-LAMMPS"\nend run'
            subprocess.run(["osascript", "-e", script, message], capture_output=True, timeout=10)
        with self.store.transaction() as db:
            self.store.set_setting(db, "notification_cursor", events["next_cursor"])

