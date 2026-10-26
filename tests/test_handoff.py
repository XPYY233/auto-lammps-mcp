import json
from pathlib import Path
import zipfile

import pytest

from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.handoff import import_project
from auto_lammps_mcp.service import Service
from auto_lammps_mcp.worker import Worker


def test_export_import_preserves_native_files_and_research_history(tmp_path):
    source = Service(tmp_path / "source")
    task = source.task_create("Ni", "Analyze earlier results and retain the fit", [{"id": "plot", "description": "curve"}], "create")
    root = source.store.root(task["id"])
    (root / "analysis.py").write_text("print('analyze existing data')\n")
    (root / "model.bin").write_bytes(b"\x00\x01\xffbinary")
    archive = Worker(source).export(task["id"])
    destination = Service(tmp_path / "destination")
    next_task = destination.task_create("Continue Ni", "Import and continue analysis", [{"id": "fit", "description": "new fit"}], "create")
    result = import_project(destination.store, next_task["id"], archive["path"])
    restored = destination.store.root(next_task["id"])
    assert (restored / "model.bin").read_bytes() == (root / "model.bin").read_bytes()
    assert result["original_request"] == task["request"]
    record = json.loads((restored / result["original_record"]).read_text())
    assert record["requirements"] == task["requirements"]
    assert record["policy"] == task["policy"]
    assert not destination.task_get(next_task["id"])["runs"]


def test_corrupt_package_is_rejected_before_project_changes(tmp_path):
    service = Service(tmp_path / "state")
    task = service.task_create("Source", "retain data", [{"id": "a", "description": "result"}], "create")
    (service.store.root(task["id"]) / "data.txt").write_text("original")
    exported = Worker(service).export(task["id"])
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(exported["path"]) as old, zipfile.ZipFile(corrupt, "w") as new:
        for name in old.namelist():
            new.writestr(name, b"replaced" if name == "project/data.txt" else old.read(name))
    other = service.task_create("Destination", "continue", [{"id": "a", "description": "result"}], "new")
    with pytest.raises(Fault, match="摘要不一致"):
        import_project(service.store, other["id"], corrupt)
    assert not list(service.store.root(other["id"]).iterdir())


def test_handoff_document_does_not_complete_archive_requirement(tmp_path):
    service = Service(tmp_path / "state")
    task = service.task_create("Research", "Give the full result package", [{"id": "package", "description": "full ZIP", "kind": "archive"}], "create")
    root = service.store.root(task["id"])
    (root / "HANDOFF.md").write_text("Files to deliver")
    (root / "check.json").write_text('{"document_exists":true}')
    registered = service.artifact_register(task["id"], 1, "HANDOFF.md", [], ["package"], [{"passed": True, "method": "document exists", "evidence": "check.json"}], "register")
    before = service.completion_check(task["id"], registered["revision"], "before-export")
    assert not before["complete"]
    assert before["missing"][0]["id"] == "package"
    exported = service.project_export(task["id"], before["revision"], "export")
    pending = service.completion_check(task["id"], before["revision"], "pending-export")
    assert not pending["complete"] and pending["active_work"] == 1
    Worker(service).work_one()
    receipt = service.operation_get(exported["operation_id"])["result"]
    after = service.completion_check(task["id"], pending["revision"], "after-export")
    assert after["complete"]
    Path(receipt["path"]).write_bytes(b"damaged archive")
    invalid = service.completion_check(task["id"], after["revision"], "damaged-export")
    assert not invalid["complete"]


def test_new_delivery_requirement_keeps_existing_plot_valid(tmp_path):
    service = Service(tmp_path / "state")
    requirements = [{"id": "plot", "description": "curve"}]
    task = service.task_create("Research", "make curve", requirements, "create")
    root = service.store.root(task["id"])
    (root / "curve.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (root / "check.txt").write_text("verified curve data")
    result = service.artifact_register(task["id"], 1, "curve.svg", [], ["plot"], [{"passed": True, "method": "verify", "evidence": "check.txt"}], "register")
    updated = service.task_update(task["id"], result["revision"], {"requirements": requirements + [{"id": "zip", "description": "archive", "kind": "archive"}]}, "also export", "add-requirement")
    assert not service.task_get(task["id"])["artifacts"][0]["stale"]
    check = service.completion_check(task["id"], updated["revision"], "check")
    assert [r["id"] for r in check["missing"]] == ["zip"]


def test_long_handoff_retains_failed_attempt_payloads_and_complete_history(tmp_path):
    from auto_lammps_mcp.records import decoded_record
    service = Service(tmp_path / "source")
    worker = Worker(service)
    task = service.task_create("Long research", "Retain every analysis attempt", [{"id": "result", "description": "result"}], "create")
    root = service.store.root(task["id"])
    (root / "data.txt").write_text("3\n")
    (root / "analyze.py").write_text("raise ValueError('wrong column')\n")
    failed = service.analysis_submit(task["id"], 1, "analyze.py", ["data.txt"], ["answer.txt"], "analysis")
    worker.work_one()
    assert service.operation_get(failed["operation_id"])["state"] == "failed"
    (root / "analyze.py").write_text("from pathlib import Path\nPath('answer.txt').write_text(Path('data.txt').read_text())\n")
    recovered = service.operation_retry(failed["operation_id"], "retry")
    worker.work_one()
    assert service.operation_get(recovered["operation_id"])["state"] == "completed"
    for n in range(35):
        current = service.task_get(task["id"])
        service.project_snapshot(task["id"], current["revision"], "snapshot-" + str(n))
        worker.work_one()
    with service.store.connect() as db:
        expected = {r["id"]: decoded_record(r) for r in db.execute("SELECT * FROM queue WHERE task_id=?", (task["id"],))}
        events = [decoded_record(r) for r in db.execute("SELECT * FROM events WHERE task_id=? ORDER BY seq", (task["id"],))]
    summary = service.task_get(task["id"])
    assert len(summary["work"]) == 5 and len(summary["events"]) == 5
    assert summary["record_counts"]["work"] == len(expected) > 35
    assert "work" in summary["records_truncated"]
    exported = worker.export(task["id"])
    with zipfile.ZipFile(exported["path"]) as archive:
        record = json.loads(archive.read("research-record.json"))
        assert {r["id"]: r for r in record["work"]} == expected
        assert sorted(record["events"], key=lambda r: r["seq"]) == events
        assert json.loads(archive.read("events.json")) == events
    destination = Service(tmp_path / "destination")
    target = destination.task_create("Continue", "Continue the long study", [{"id": "result", "description": "result"}], "create")
    imported = import_project(destination.store, target["id"], exported["path"])
    restored = json.loads((destination.store.root(target["id"]) / imported["original_record"]).read_text())
    assert {r["id"]: r for r in restored["work"]} == expected
    assert restored["request"] == task["request"]
    assert expected[failed["operation_id"]]["state"] == "failed"
    assert expected[failed["operation_id"]]["payload"]["inputs"] == ["data.txt"]
