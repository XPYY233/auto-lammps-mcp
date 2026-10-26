import json
from pathlib import Path
import subprocess
import threading
import time

import pytest

from auto_lammps_mcp.remote_read import read_file
from auto_lammps_mcp.slurm import Slurm
from conftest import prepare, revision


def test_log_reports_latest_complete_block_and_raw_time(tmp_path):
    (tmp_path / "log.lammps").write_text("Step Temp\n0 300\n100 301\nLoop time\nStep Time TotEng\n0 0 -12\n50 0.05 -11.9\n100 0.1")
    observed = read_file(tmp_path, "log.lammps")
    assert observed["thermo"]["values"] == {"Step": 50.0, "Time": 0.05, "TotEng": -11.9}
    assert observed["modified_at"] <= observed["observed_at"]
    assert observed["next_offset"] == observed["bytes"]


def test_bounded_tail_does_not_invent_missing_column_header(tmp_path):
    path = tmp_path / "log.lammps"
    path.write_text("Step Time Temp\n" + "".join(f"{i} {i / 1000} 300\n" for i in range(10000)))
    tail = read_file(tmp_path, "log.lammps", limit=128)
    assert tail["offset"] > 0 and tail["truncated"]
    assert tail["thermo"] is None
    first = read_file(tmp_path, "log.lammps", offset=0, limit=100)
    next_page = read_file(tmp_path, "log.lammps", offset=first["next_offset"], limit=100)
    assert (first["text"] + next_page["text"]).encode() == path.read_bytes()[:200]


def test_log_reader_refuses_paths_outside_original_run(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    (tmp_path / "outside").write_text("other project")
    (root / "link").symlink_to(tmp_path / "outside")
    for path in ("../outside", "link", str(tmp_path / "outside")):
        with pytest.raises(ValueError):
            read_file(root, path)
    assert read_file(root, "not-yet-written.log")["exists"] is False


def test_scheduler_and_log_observation_share_one_remote_query(tmp_path, service):
    (tmp_path / "log.lammps").write_text("Step Time Temp\n200 0.2 300\n")

    class Shell:
        calls = 0

        def command(self, alias, script):
            self.calls += 1
            scheduler = "sacct() { printf '123|RUNNING|0:0|20|32|run_a|2026-10-27T01:00:00\\n'; }\n"
            return subprocess.run(["bash", "-s"], input=scheduler + script, text=True, capture_output=True, check=True).stdout

    remote = Shell()
    result = Slurm(service.store, remote).query("cluster", [dict(id="run_a", job_id="123", remote_dir=str(tmp_path))])
    assert remote.calls == 1
    assert result["123"]["state"] == "running"
    assert result["123"]["core_seconds"] == 640
    files = result["123"]["output_progress"]["files"]
    assert files[0]["thermo"]["values"]["Step"] == 200
    assert files[1]["exists"] is False


def test_queued_log_read_reuses_operation_and_never_submits(service, task, worker, scheduler, monkeypatch):
    run = prepare(service, worker, task)
    monkeypatch.setattr(scheduler, "read_log", lambda *args: dict(files=[dict(text="existing output", next_offset=15)]), raising=False)
    first = service.run_log(run["id"], "read-log")
    assert service.run_log(run["id"], "read-log") == first
    worker.work_one()
    assert service.operation_get(first["operation_id"])["result"]["files"][0]["text"] == "existing output"
    assert scheduler.submissions == 0


def test_upload_keeps_analysis_and_stop_responsive(service, task, worker, scheduler, monkeypatch):
    run = prepare(service, worker, task)
    service.run_submit(run["id"], revision(service, task), "submit")
    entered, release = threading.Event(), threading.Event()

    def slow_stage(run):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(scheduler, "stage", slow_stage)
    root = service.store.root(task["id"])
    (root / "input.txt").write_text("existing data analyzed")
    (root / "analyze.py").write_text("from pathlib import Path\nPath('result.txt').write_text(Path('input.txt').read_text())\n")
    queued = service.analysis_submit(task["id"], revision(service, task), "analyze.py", ["input.txt"], ["result.txt"], "analysis")
    thread = threading.Thread(target=worker.loop)
    thread.start()
    try:
        assert entered.wait(2)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and service.operation_get(queued["operation_id"])["state"] != "completed":
            time.sleep(.05)
        assert service.operation_get(queued["operation_id"])["state"] == "completed"
        progress = service.run_get(run["id"])["evidence"]["preparation"]
        assert progress["phase"] == "uploading" and progress["observed_at"] >= progress["started_at"]
        service.task_control(task["id"], revision(service, task), "stop", "stop-upload")
        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not any(e["kind"] == "staging_finished" for e in service.events()["events"]):
            time.sleep(.05)
        assert scheduler.submissions == 0
        assert service.run_get(run["id"])["state"] == "cancelled_before_dispatch"
    finally:
        release.set()
        worker.stopping.set()
        thread.join(timeout=5)
