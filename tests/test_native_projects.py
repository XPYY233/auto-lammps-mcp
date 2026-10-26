import json
from pathlib import Path
import zipfile

import pytest
from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.files import file_hash
from conftest import revision


def test_import_preserves_binary_helpers_and_relative_paths(service, task, worker, tmp_path):
    source = tmp_path / "source"
    (source / "models").mkdir(parents=True)
    (source / "models/model.bin").write_bytes(bytes(range(256)))
    (source / "run.sh").write_text("python helper.py models/model.bin\n")
    (source / "helper.py").write_text("print('helper')\n")
    op = service.project_import(task["id"], 1, str(source), "import")
    worker.work_one()
    assert service.operation_get(op["operation_id"])["state"] == "completed"
    assert (service.store.root(task["id"]) / "models/model.bin").read_bytes() == bytes(range(256))


def test_patch_is_local_and_reports_complete_file_pagination(service, task):
    first = service.file_patch(task["id"], 1, "in.lmp", None, "", "units metal\nrun 100\n", "new")
    second = service.file_patch(task["id"], 2, "in.lmp", first["sha256"], "run 100", "run 200", "edit")
    assert "units metal" in service.file_read(task["id"], "in.lmp")["text"]
    assert "-run 100" in second["diff"]
    page = service.file_read(task["id"], "in.lmp", limit=5)
    assert page["truncated"] and page["next_offset"] == 5
    with pytest.raises(Fault, match="已变化"):
        service.file_patch(task["id"], 3, "in.lmp", first["sha256"], "100", "300", "stale")


def test_path_escape_and_external_symlink_rejected(service, task, tmp_path):
    with pytest.raises(Fault):
        service.file_read(task["id"], "../secret")
    secret = tmp_path / "secret"
    secret.write_text("outside")
    (service.store.root(task["id"]) / "link").symlink_to(secret)
    with pytest.raises(Fault):
        service.file_read(task["id"], "link")


def register(service, task, name, sources, rid, op):
    root = service.store.root(task["id"])
    (root / name).write_text("artifact")
    (root / "check.json").write_text('{"passed":true}')
    return service.artifact_register(task["id"], revision(service, task), name, sources, [rid], [{"passed": True, "method": "fixture validation", "evidence": "check.json"}], op)


def test_missing_user_output_cannot_complete(service, task):
    result = service.completion_check(task["id"], 1, "complete")
    assert result["complete"] is False and result["missing"][0]["id"] == "curve"


def test_changed_input_invalidates_transitive_results(service, task):
    root = service.store.root(task["id"])
    (root / "data.csv").write_text("x,y\n1,2\n")
    register(service, task, "plot.svg", ["data.csv"], "curve", "plot")
    register(service, task, "report.txt", ["plot.svg"], "curve", "report")
    assert service.completion_check(task["id"], revision(service, task), "complete")["complete"]
    (root / "data.csv").write_text("x,y\n1,3\n")
    assert not service.completion_check(task["id"], revision(service, task), "changed")["complete"]
    assert all(a["stale"] for a in service.task_get(task["id"])["artifacts"])


def test_preview_checks_upstream_data_without_completion_call(service, task):
    root = service.store.root(task["id"])
    (root / "data.csv").write_text("1,2\n")
    register(service, task, "plot.svg", ["data.csv"], "curve", "plot")
    register(service, task, "report.txt", ["plot.svg"], "curve", "report")
    (root / "data.csv").write_text("1,3\n")
    assert service.artifact_preview(task["id"], "report.txt")["stale"]


def test_reregistered_sources_replace_old_staleness(service, task):
    root = service.store.root(task["id"])
    (root / "data.csv").write_text("1,2\n")
    register(service, task, "plot.svg", ["data.csv"], "curve", "plot-old")
    register(service, task, "report.txt", ["plot.svg"], "curve", "report-old")
    previous = service.file_read(task["id"], "data.csv")
    service.file_patch(task["id"], revision(service, task), "data.csv", previous["sha256"], "1,2", "1,3", "edit")
    assert all(a["stale"] for a in service.task_get(task["id"])["artifacts"])
    register(service, task, "plot.svg", ["data.csv"], "curve", "plot-new")
    register(service, task, "report.txt", ["plot.svg"], "curve", "report-new")
    service.completion_check(task["id"], revision(service, task), "complete-new")
    latest = service.task_get(task["id"])["artifacts"][-2:]
    assert all(not a["stale"] for a in latest)


def test_analysis_failure_preserves_data_then_retry(service, task, worker):
    root = service.store.root(task["id"])
    (root / "data.txt").write_text("3\n")
    (root / "analyze.py").write_text("raise ValueError('wrong column')\n")
    job = service.analysis_submit(task["id"], 1, "analyze.py", ["data.txt"], ["answer.txt"], "analyze")
    worker.work_one()
    assert service.operation_get(job["operation_id"])["state"] == "failed"
    assert (root / "data.txt").read_text() == "3\n"
    (root / "analyze.py").write_text("from pathlib import Path\nPath('answer.txt').write_text(Path('data.txt').read_text())\n")
    retry = service.operation_retry(job["operation_id"], "retry")
    worker.work_one()
    assert service.operation_get(retry["operation_id"])["state"] == "completed"
    assert service.operation_get(job["operation_id"])["state"] == "failed"


def test_export_contains_full_history_and_files(service, task, worker):
    root = service.store.root(task["id"])
    (root / "data.txt").write_text("original research data")
    job = service.project_export(task["id"], 1, "export")
    worker.work_one()
    result = service.operation_get(job["operation_id"])["result"]
    with zipfile.ZipFile(result["path"]) as archive:
        assert archive.read("project/data.txt") == b"original research data"
        assert json.loads(archive.read("research-record.json"))["request"] == task["request"]
        assert json.loads(archive.read("events.json"))


def test_unread_progress_is_per_consumer_and_acknowledged_explicitly(service, task):
    first = service.events(consumer="codex")
    assert first["events"]
    assert service.events(consumer="codex")["events"] == first["events"]
    service.events_ack("codex", first["next_cursor"])
    assert not service.events(consumer="codex")["events"]
    assert service.events(consumer="dsh")["events"]

def test_task_summary_does_not_repeat_large_snapshot_payloads(service, task):
    with service.store.transaction() as db:
        work = service.store.queue(db, task["id"], "snapshot", {})
        result = {"files": {f"data/{n}.csv": {"sha256": "a" * 64, "bytes": 100} for n in range(500)}}
        db.execute("UPDATE queue SET state='completed',result=? WHERE id=?", (json.dumps(result), work["operation_id"]))
        service.store.event(db, task["id"], "work_completed", {"operation_id": work["operation_id"], "kind": "snapshot", "result": result})
    summary = service.task_get(task["id"])
    assert len(json.dumps(summary)) < 8000
    assert summary["request"] == task["request"]
    assert service.operation_get(work["operation_id"])["result"] == result
    page = service.task_records(task["id"], "work", limit=1)
    assert page["records"][0]["result"] == result
    assert page["next_offset"] == 1 and not page["more"]


def test_multistage_task_summary_fits_host_response_and_retains_history(service, task):
    with service.store.transaction() as db:
        for number in range(20):
            service.store.event(db, task["id"], "work_completed", {
                "operation_id": "work_" + str(number).zfill(20), "kind": "analysis",
                "result": {"path": "analysis/" + str(number).zfill(20) + "/response_statistics.json",
                           "message": "The existing results were checked and preserved for the next analysis."}})
    summary = service.task_get(task["id"])
    assert len(json.dumps(summary,ensure_ascii=False,indent=2)) < 6000
    assert summary["request"] == task["request"]
    assert "events" in summary["records_truncated"]
    assert service.events(task_id=task["id"])["events"][-1]["data"]["operation_id"].endswith("19")

