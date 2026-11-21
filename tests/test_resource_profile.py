import json

import pytest

from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.resource_profile import resources


def test_scoped_resources(monkeypatch, tmp_path, service):
    profile = tmp_path / "resources.json"
    profile.write_text(json.dumps({"cores": 1, "memory_mib": 2048, "wall_seconds": 900}))
    monkeypatch.setenv("ALM_RESOURCE_PROFILE", str(profile))
    task = service.task_create("Study", "Study a sample", [{"id": "data", "description": "data"}], "create")
    assert all(task["policy"][key] == value for key, value in resources().items())
    for i, invalid in enumerate(({"cores": 32}, {"memory_mib": 8192}, {"cores": True}, {"wall_seconds": 901})):
        with pytest.raises(Fault):
            service.task_update(task["id"], task["revision"], {"policy": invalid}, "check", f"invalid-{i}")


def test_default_and_malformed_profile(monkeypatch, tmp_path):
    monkeypatch.delenv("ALM_RESOURCE_PROFILE", raising=False)
    assert resources() == {"cores": 32, "memory_mib": 8192, "wall_seconds": 604800}
    profile = tmp_path / "resources.json"
    profile.write_text(json.dumps({"cores": 1, "memory_mib": True, "wall_seconds": 600}))
    monkeypatch.setenv("ALM_RESOURCE_PROFILE", str(profile))
    with pytest.raises(Fault):
        resources()
