import json
import sqlite3
import time

import pytest

from auto_lammps_mcp.backup import create_backup, restore_backup
from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.service import Service
from auto_lammps_mcp.worker import Worker


def test_backup_restore_preserves_populated_data_and_holds_execution(tmp_path):
    original = Service(tmp_path / "original")
    task = original.task_create("Research", "preserve original request", [{"id": "a", "description": "result"}], "create")
    root = original.store.root(task["id"])
    (root / "data.bin").write_bytes(b"\x00\x01\xff")
    with original.store.transaction() as db:
        original.store.event(db, task["id"], "retained_failure", {"attempt": 1, "cost": 12.3})
        original.store.set_setting(db, "process:old", dict(pid=1, started="old process"))
        original.store.set_setting(db, "service_heartbeat", dict(boot_id="old-controller", observed_at=time.time()))
    request = original.backup_create(str(tmp_path / "archive.zip"), "backup")
    Worker(original).work_one()
    outcome = original.operation_get(request["operation_id"])
    assert outcome["state"] == "completed"
    assert original.backup_create(str(tmp_path / "archive.zip"), "backup") == request
    receipt = outcome["result"]
    restored_home = tmp_path / "restored"
    restore_backup(receipt["path"], receipt["sha256"], restored_home)
    restored = Service(restored_home)
    recovered = restored.task_get(task["id"])
    assert recovered["request"] == task["request"]
    assert (restored.store.root(task["id"]) / "data.bin").read_bytes() == b"\x00\x01\xff"
    assert any(e["kind"] == "retained_failure" for e in recovered["events"])
    assert restored.doctor()["recovery"]["pending"] is True
    assert restored.store.setting("process:old") is None
    assert restored.doctor()["daemon"]["heartbeat"] is None
    assert restored.operation_get(request["operation_id"])["state"] == "interrupted"
    assert restored.recovery_activate(True, "activate")["activated"]
    assert restored.recovery_activate(True, "activate")["activated"]


def test_backup_refuses_active_writer_and_bad_hash(tmp_path):
    service = Service(tmp_path / "state")
    task = service.task_create("Research", "keep file", [{"id": "a", "description": "result"}], "create")
    claim = service.task_claim(task["id"], "writer")
    with pytest.raises(Fault, match="项目正在修改"):
        create_backup(service.store, tmp_path / "busy.zip")
    assert not (tmp_path / "busy.zip").exists()
    service.task_release(task["id"], claim["token"])
    receipt = create_backup(service.store, tmp_path / "good.zip")
    with pytest.raises(Fault, match="摘要与回执不一致"):
        restore_backup(receipt["path"], "wrong", tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
