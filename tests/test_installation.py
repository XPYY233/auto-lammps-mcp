import json
from pathlib import Path
import subprocess

import pytest

from auto_lammps_mcp.core import Fault, Store
from auto_lammps_mcp.dsh import connect, update_patch


def test_dsh_upgrade_preserves_model_and_other_tools():
    original = '# User settings\n- id: agent-default-model\n  config:\n    provider: deepseek-official\n    model: selected-model\n'
    first = update_patch(original, dict(id="mcp-auto-lammps", config=dict(command="version-one")))
    second = update_patch(first, dict(id="mcp-auto-lammps", config=dict(command="version-two")))
    assert original.strip() in second
    assert "version-one" not in second
    assert second.count("mcp-auto-lammps") == 1
    assert update_patch(second, dict(id="mcp-auto-lammps", config=dict(command="version-two"))) == second


def test_dsh_invalid_composition_restores_original(tmp_path, monkeypatch):
    profile = tmp_path / "dsh/profiles/web/cordis.patch.yml"
    profile.parent.mkdir(parents=True)
    original = b"- id: configured-model\n  disabled: false\n"
    profile.write_bytes(original)
    launcher = tmp_path / "dsh-cli"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o700)

    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0 if "--version" in args else 1, "0.1.7" if "--version" in args else "", "private model config")

    monkeypatch.setattr(subprocess, "run", run)
    store = Store(tmp_path / "alm")
    with pytest.raises(Fault) as error:
        connect(store, dict(command="alm", args=[]), str(launcher), dsh_home=str(tmp_path / "dsh"))
    assert error.value.code == "dsh_configuration_failed"
    assert profile.read_bytes() == original
    assert "private model config" not in str(error.value)
    assert next((store.home / "installation-backups").glob("*.yml")).read_bytes() == original


def test_dsh_connect_records_runtime_and_keeps_credentials_at_host(tmp_path, monkeypatch):
    home = tmp_path / "dsh"
    profile = home / "profiles/web/cordis.patch.yml"
    profile.parent.mkdir(parents=True)
    profile.write_text("[]\n")
    credentials = home / ".credentials.yaml"
    credentials.write_bytes(b"opaque host credential store")
    launcher = tmp_path / "dsh-cli"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o700)
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "0.1.7" if "--version" in args else "mcp-auto-lammps", ""))
    store = Store(tmp_path / "alm")
    result = connect(store, dict(command="alm", args=["mcp"]), str(launcher), dsh_home=str(home))
    assert result["runtime_version"] == "0.1.7"
    assert credentials.read_bytes() == b"opaque host credential store"
    assert not list(store.home.rglob("*credential*"))
    assert '"command": "alm"' in Path(result["overlay"]).read_text()
