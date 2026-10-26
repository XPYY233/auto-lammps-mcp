from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import Mock

import pytest

from auto_lammps_mcp.activity import activity_lock
from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.installation import install
from conftest import prepare, revision


def test_upgrade_preserves_host_files_during_active_upload(service, task, worker, scheduler, tmp_path, monkeypatch):
    run = prepare(service, worker, task)
    service.run_submit(run["id"], revision(service, task), "submit")
    entered, release = threading.Event(), threading.Event()

    def stage(run):
        entered.set()
        assert release.wait(10)

    monkeypatch.setattr(scheduler, "stage", stage)
    user = tmp_path / "user"
    guide = user / ".codex/skills/auto-lammps-mcp/SKILL.md"
    guide.parent.mkdir(parents=True)
    guide.write_text("current installed guide")
    monkeypatch.setattr(Path, "home", lambda: user)
    thread = threading.Thread(target=worker.dispatch_one)
    thread.start()
    assert entered.wait(2)
    commands = Mock(side_effect=AssertionError("must not change host or service"))
    monkeypatch.setattr("auto_lammps_mcp.installation.subprocess.run", commands)
    try:
        with pytest.raises(Fault) as error:
            install(service.store, host="codex", managed=True)
        assert error.value.code == "service_busy"
        assert error.value.effect == "rejected_before_action"
        assert guide.read_text() == "current installed guide"
        assert not service.store.setting("hosts")
        commands.assert_not_called()
    finally:
        worker.stopping.set()
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive() and scheduler.submissions == 0
    with activity_lock(service.store.home, replacement=True) as acquired:
        assert acquired


def test_replacement_holds_new_dispatch_and_work_then_resumes(service, task, worker, scheduler):
    run = prepare(service, worker, task)
    service.run_submit(run["id"], revision(service, task), "submit")
    snapshot = service.project_snapshot(task["id"], revision(service, task), "next-snapshot")
    with activity_lock(service.store.home, replacement=True) as acquired:
        assert acquired
        worker.dispatch_one()
        worker.work_one()
        assert scheduler.staged == [] and scheduler.submissions == 0
        assert service.operation_get(snapshot["operation_id"])["state"] == "pending"
        assert service.run_get(run["id"])["state"] == "pending"
        assert service.task_get(task["id"])["request"] == task["request"]
    worker.work_one()
    worker.dispatch_one()
    assert service.operation_get(snapshot["operation_id"])["state"] == "completed"
    assert service.run_get(run["id"])["state"] == "queued"
    assert scheduler.submissions == 1


def test_interrupted_installer_releases_replacement_ownership(service):
    source = """from pathlib import Path
import sys
from auto_lammps_mcp.activity import activity_lock
with activity_lock(Path(sys.argv[1]), replacement=True) as acquired:
    print(acquired, flush=True)
    sys.stdin.read()
"""
    process = subprocess.Popen([sys.executable, "-c", source, str(service.store.home)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "True"
        with activity_lock(service.store.home) as acquired:
            assert not acquired
        process.kill()
        process.wait(timeout=5)
        with activity_lock(service.store.home) as acquired:
            assert acquired
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def test_legacy_service_checks_accepted_dispatch_before_host_changes(service, task, worker, monkeypatch):
    run = prepare(service, worker, task)
    service.run_submit(run["id"], revision(service, task), "submit")
    monkeypatch.setattr("auto_lammps_mcp.transport.call", lambda *a, **k: {"python": "previous-runtime"})
    commands = Mock(side_effect=AssertionError("legacy run must finish first"))
    monkeypatch.setattr("auto_lammps_mcp.installation.subprocess.run", commands)
    with pytest.raises(Fault) as error:
        install(service.store, host="codex", managed=True)
    assert error.value.code == "service_busy"
    assert service.run_get(run["id"])["state"] == "pending"
    assert not service.store.setting("hosts")
    commands.assert_not_called()
