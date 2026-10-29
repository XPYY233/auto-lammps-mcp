"""Accounted SSH/Slurm transport. Dispatch ambiguity is never treated as rejection."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from .core import Fault, encode, require
from .files import atomic_write, materialize

ACTIVE = ("dispatching", "unknown", "queued", "running", "cancelling")
TERMINAL = ("completed", "failed", "cancelled", "timeout")
STATES = {"PENDING": "queued", "CONFIGURING": "queued", "RUNNING": "running", "COMPLETING": "running",
          "SUSPENDED": "running", "REQUEUED": "queued", "COMPLETED": "completed", "FAILED": "failed",
          "CANCELLED": "cancelled", "TIMEOUT": "timeout", "OUT_OF_MEMORY": "failed", "NODE_FAIL": "failed",
          "PREEMPTED": "failed", "BOOT_FAIL": "failed", "DEADLINE": "timeout"}


class SSH:
    def command(self, alias, script, timeout=45):
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", alias), "连接名称无效")
        try:
            result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", alias, "bash -l -s"], input=script, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise Fault("connection_timeout", "远端操作未返回回执", "核对远端状态后接续", "unknown") from exc
        if result.returncode:
            raise Fault("remote_command_failed", result.stderr[-8000:] or result.stdout[-8000:], "读取已保存操作并核对远端状态", "unknown")
        return result.stdout

    def transfer(self, alias, local, remote, upload):
        require(re.fullmatch(r"/[A-Za-z0-9_./-]+", remote), "远端路径无效")
        source, target = (str(local) + "/", alias + ":" + remote + "/") if upload else (alias + ":" + remote + "/", str(local) + "/")
        result = subprocess.run(["rsync", "-rlt", "--safe-links", "--partial", "--checksum", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15", source, target], capture_output=True, text=True, timeout=3600)
        if result.returncode:
            raise Fault("transfer_interrupted", result.stderr[-8000:], "从已传输文件继续同步", "partial")


class Slurm:
    def __init__(self, store, ssh=None):
        self.store, self.ssh = store, ssh or SSH()

    def connection_check(self, config):
        script = "set -e\n" + "command -v sbatch\ncommand -v sacct\ncommand -v rsync\n" + "mkdir -p " + shlex.quote(config["remote_root"]) + "\ntest -w " + shlex.quote(config["remote_root"]) + "\nsinfo -h -o '%P|%a|%l'\n"
        return dict(output=self.ssh.command(config["ssh_alias"], script), ssh_alias=config["ssh_alias"])

    def stage(self, run):
        environment = json.loads(run["environment"])
        rules = json.loads(run["rules"])
        with self.store.connect() as db:
            row = db.execute("SELECT manifest FROM snapshots WHERE id=?", (run["snapshot_id"],)).fetchone()
            plan = db.execute('SELECT p.specification FROM validation_plans p JOIN run_validations v ON v.plan_id=p.id WHERE v.run_id=?', (run['id'],)).fetchone()
        monitor = json.loads(plan[0]).get('monitor', []) if plan else []
        with tempfile.TemporaryDirectory(prefix="alm-stage-") as directory:
            root = Path(directory)
            materialize(self.store, json.loads(row[0]), root)
            command = 'bash ' + shlex.quote(run['entry'])
            if monitor:
                atomic_write(root / '.alm-guard.py', Path(__file__).with_name('node_guard.py').read_bytes())
                atomic_write(root / '.alm-guard-spec.json', encode(dict(checks=monitor)).encode())
                command = 'python3 .alm-guard.py .alm-guard-spec.json ' + shlex.quote(run['entry'])
            seconds = rules["wall_seconds"]
            walltime = f"{seconds // 86400}-{seconds % 86400 // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
            # Environment setup intentionally precedes nounset: module initializers may inspect unset variables.
            script = "\n".join(["#!/bin/bash", f"#SBATCH --job-name={run['id']}", f"#SBATCH --account={environment['account']}",
                f"#SBATCH --partition={environment['partition']}", "#SBATCH --nodes=1", f"#SBATCH --ntasks={rules['cores']}",
                f"#SBATCH --mem={rules['memory_mib']}M", f"#SBATCH --time={walltime}", "#SBATCH --output=.alm-slurm-%j.out", "#SBATCH --export=NONE",
                "set -eo pipefail", "source /etc/profile", environment["environment_script"],
                "export SLURM_EXPORT_ENV=ALL", "set -u", "cd " + shlex.quote(run["remote_dir"]),
                command, ""])
            atomic_write(root / ".alm-job.sh", script.encode())
            atomic_write(root / ".alm-input.json", encode(dict(run_id=run["id"], snapshot_id=run["snapshot_id"], rules=rules, environment=environment)).encode())
            self.ssh.command(run["cluster"], "mkdir -p " + shlex.quote(run["remote_dir"]))
            self.ssh.transfer(run["cluster"], root, run["remote_dir"], upload=True)

    def submit(self, run):
        # The remote guard survives connection loss and refuses a second sbatch, even if both replies were lost.
        script = "\n".join(["set -e", "cd " + shlex.quote(run["remote_dir"]),
            "if test -s .alm-receipt; then cat .alm-receipt;",
            "elif mkdir .alm-submit-once 2>/dev/null; then",
            "  sbatch --parsable .alm-job.sh > .alm-receipt.tmp",
            "  mv .alm-receipt.tmp .alm-receipt", "  cat .alm-receipt",
            "else echo 'submission_already_started' >&2; exit 75; fi", ""])
        response = self.ssh.command(run["cluster"], script).strip()
        require(re.fullmatch(r"[0-9]+(?:;[A-Za-z0-9_.-]+)?", response), "提交回执无法解析", "submission_unknown", "按原作业名称查找，禁止再次提交")
        return response.split(";")[0]

    def query(self, alias, runs):
        ids = [r["job_id"] for r in runs if r["job_id"]]
        require(all(re.fullmatch(r"[0-9]+", x) for x in ids), "作业编号无效")
        if not ids:
            return {}
        requests = [dict(run_id=r["id"], root=r["remote_dir"], paths=["log.lammps", '.alm-guard.json', ".alm-slurm-" + r["job_id"] + ".out"]) for r in runs if r["job_id"]]
        output = self.ssh.command(alias, "sacct -n -P -X -j " + ",".join(ids) + " --format=JobIDRaw,State,ExitCode,ElapsedRaw,AllocCPUS,JobName%100,Submit\n" + self.output_script(requests))
        progress = self.output_records(output)
        found = {}
        expected = {r["job_id"]: r["id"] for r in runs}
        for line in output.splitlines():
            parts = line.split("|")
            if len(parts) < 7 or parts[0] not in expected:
                continue
            job_id, rawstate, exitcode, elapsed, cpus, name, submitted = parts[:7]
            require(name == expected[job_id], "调度作业身份不一致", "job_identity_mismatch", "按原作业名和提交时间核对")
            state = STATES.get(rawstate.split()[0].rstrip("+"), "unknown")
            found[job_id] = dict(state=state, scheduler_state=rawstate, exit_code=exitcode,
                elapsed_seconds=int(elapsed or 0), allocated_cores=int(cpus or 0),
                core_seconds=int(elapsed or 0) * int(cpus or 0), submitted=submitted)
            if name in progress:
                found[job_id]["output_progress"] = progress[name]
        return found

    @staticmethod
    def output_script(requests):
        source = Path(__file__).with_name("remote_read.py").read_text()
        return ("if command -v python3 >/dev/null 2>&1; then\npython3 - " + shlex.quote(json.dumps(requests)) +
                " <<'ALM_OUTPUT_READER'\n" + source + "\nALM_OUTPUT_READER\nelse\necho 'ALM_READER_UNAVAILABLE'\nfi\n")

    @staticmethod
    def output_records(output):
        records = {}
        for line in output.splitlines():
            if line.startswith("ALM_OUTPUT\t"):
                value = json.loads(line.split("\t", 1)[1])
                records[value["run_id"]] = value
        return records

    def read_log(self, run, path, offset=None, limit=65536):
        request = dict(run_id=run["id"], root=run["remote_dir"], paths=[path], offset=offset, limit=limit)
        output = self.ssh.command(run["cluster"], self.output_script([request]))
        records = self.output_records(output)
        require(run["id"] in records, "远端日志读取器未返回数据", "log_reader_unavailable", "检查远端 Python 3，或回收指定输出后读取")
        return records[run["id"]]

    def reconcile(self, run):
        start = datetime.fromtimestamp(run["created"] - 86400, timezone.utc).strftime("%Y-%m-%d")
        name = run["id"]
        output = self.ssh.command(run["cluster"], "squeue --me -h -o '%i|%j'\nsacct -n -P -X --starttime=" + start + " --name=" + shlex.quote(name) + " --format=JobIDRaw,JobName%100\n")
        matches = set()
        for line in output.splitlines():
            parts = line.split("|")
            if len(parts) >= 2 and parts[1] == name and parts[0].isdigit():
                matches.add(parts[0])
        require(len(matches) <= 1, "同一执行意图对应多个远端作业", "duplicate_jobs", "停止后续派发并核对远端作业")
        return next(iter(matches), None)

    def collect(self, run):
        destination = self.store.path(run["task_id"], "runs/" + run["id"])
        destination.mkdir(parents=True, exist_ok=True)
        self.ssh.transfer(run["cluster"], destination, run["remote_dir"], upload=False)
        return dict(directory=str(destination), run_id=run["id"])

    def cancel(self, run):
        if run["job_id"]:
            require(re.fullmatch(r"[0-9]+", run["job_id"]), "作业编号无效")
            self.ssh.command(run["cluster"], "scancel " + run["job_id"] + "\n")

