import json
from pathlib import Path
import subprocess
import sys

import pytest

from auto_lammps_mcp.core import Fault, Store
from auto_lammps_mcp.dsh import connect, update_patch
from auto_lammps_mcp.installation import install


def test_runtime_identity_tracks_linked_skill_guides(tmp_path, monkeypatch):
    from auto_lammps_mcp import identity
    root = tmp_path / "package"
    guide = root / "data/skill"
    (guide / "references").mkdir(parents=True)
    (root / "identity.py").write_text("fixed code")
    (guide / "SKILL.md").write_text("Read references/delivery.md")
    detail = guide / "references/delivery.md"
    detail.write_text("original procedure")
    monkeypatch.setattr(identity, "__file__", str(root / "identity.py"))
    identity.runtime_identity.cache_clear()
    try:
        original = identity.runtime_identity()
        detail.write_text("updated procedure")
        identity.runtime_identity.cache_clear()
        changed = identity.runtime_identity()
        assert original["skill_sha256"] == changed["skill_sha256"]
        assert original["code_sha256"] == changed["code_sha256"]
        assert original["skill_bundle_sha256"] != changed["skill_bundle_sha256"]
        assert original["fingerprint"] != changed["fingerprint"]
        assert identity.connection_status({"client": original}, changed)["state"] == "reload_required"
    finally:
        identity.runtime_identity.cache_clear()


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


def test_installer_rollback_uses_previous_binary_and_keeps_data(tmp_path):
    prefix = tmp_path / "install"
    previous = prefix / "versions/previous"
    current = prefix / "versions/current"
    for version in (previous, current):
        (version / "bin").mkdir(parents=True)
        executable = version / "bin/alm"
        executable.write_text("#!/bin/sh\nprintf '%s\\n' '{\"version\":\"retained\",\"daemon\":{\"responding\":true}}'\n")
        executable.chmod(0o700)
    (prefix / "current").symlink_to(current)
    (prefix / "installation-progress.json").write_text(json.dumps(dict(steps=dict(complete=dict(previous=str(previous))))))
    data = tmp_path / "research"
    data.mkdir()
    (data / "result.txt").write_text("keep research")
    installer = Path(__file__).resolve().parents[1] / "scripts/install.py"
    result = subprocess.run([sys.executable, str(installer), "--rollback", "--prefix", "install", "--home", "research", "--no-launchd"], cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert Path(receipt["binary"]) == previous / "bin/alm"
    assert (prefix / "current").resolve() == previous
    assert (data / "result.txt").read_text() == "keep research"
    progress = json.loads((prefix / "installation-progress.json").read_text())
    assert progress["steps"]["complete"]["previous"] == str(current)


def test_installer_failure_returns_parseable_recovery_receipt(tmp_path):
    installer = Path(__file__).resolve().parents[1] / "scripts/install.py"
    result = subprocess.run([sys.executable, str(installer), "--rollback", "--prefix", str(tmp_path)], text=True, capture_output=True)
    assert result.returncode == 1
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "installation_failed"
    assert "没有保留" in error["message"]
    assert error["next_action"]


def test_install_without_host_preserves_global_skills(tmp_path, monkeypatch):
    from auto_lammps_mcp.identity import runtime_identity
    user = tmp_path / "user"
    existing = user / ".agents/skills/auto-lammps-mcp/SKILL.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("existing selected host version")
    binary = tmp_path / "version/bin/alm"
    binary.parent.mkdir(parents=True)
    binary.write_text("installed entry")
    monkeypatch.setattr(Path, "home", lambda: user)
    monkeypatch.setattr(sys, "executable", str(binary.with_name("python")))
    runtime_identity.cache_clear()
    try:
        receipt = install(Store(tmp_path / "state"), host="none", managed=False)
    finally:
        runtime_identity.cache_clear()
    assert existing.read_text() == "existing selected host version"
    assert Path(receipt["hosts"]["none"]["skill"]) != existing.parent
    assert (Path(receipt["hosts"]["none"]["skill"]) / "SKILL.md").is_file()
