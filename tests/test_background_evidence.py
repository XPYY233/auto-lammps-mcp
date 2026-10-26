import json

import pytest

from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.service import Service
from auto_lammps_mcp.worker import Worker


def test_zero_exit_summary_cannot_replace_real_task_read(tmp_path):
    service = Service(tmp_path / "state")
    task = service.task_create("install", "verify background", [{"id": "a", "description": "handoff"}], "create")
    log = tmp_path / "events.jsonl"
    log.write_text(json.dumps(dict(type="item.completed", item=dict(type="mcp_tool_call", tool="task_get", arguments=dict(task_id=task["id"]), status="failed", error=dict(message="approval policy is never")))) + "\n")
    with pytest.raises(Fault, match="实际工具回执"):
        Worker(service).verify_background_output(task["id"], log, [])
    log.write_text(json.dumps(dict(type="item.completed", item=dict(type="mcp_tool_call", tool="task_get", arguments=dict(task_id=task["id"]), status="completed", result=dict(isError=False)))) + "\n")
    with pytest.raises(Fault, match="后台成果缺失"):
        Worker(service).verify_background_output(task["id"], log, ["handoff.md"])
    (service.store.root(task["id"]) / "handoff.md").write_text("actual handoff")
    result = Worker(service).verify_background_output(task["id"], log, ["handoff.md"])
    assert result["task_read"]
    assert result["outputs"]["handoff.md"]["sha256"]


def test_old_summary_receipt_is_not_reported_as_verified(tmp_path):
    service = Service(tmp_path / "state")
    with service.store.transaction() as db:
        service.store.set_setting(db, "background_receipt", dict(summary="tools denied"))
    assert service.doctor()["background"]["receipt"] is None


def test_background_inherits_system_route_without_overwriting_explicit_proxy(monkeypatch):
    import subprocess
    from auto_lammps_mcp import network
    monkeypatch.setattr(network.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(network.os, "environ", {"HTTP_PROXY": "http://explicit.example:3128"})
    monkeypatch.setattr(network.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, "HTTPEnable : 1\nHTTPProxy : localhost\nHTTPPort : 7890\nHTTPSEnable : 1\nHTTPSProxy : localhost\nHTTPSPort : 7890\n", ""))
    env = network.background_environment()
    assert env["HTTP_PROXY"] == "http://explicit.example:3128"
    assert env["HTTPS_PROXY"] == "http://localhost:7890"
    assert "127.0.0.1" in env["NO_PROXY"]
