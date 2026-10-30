import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from auto_lammps_mcp import slurm
from auto_lammps_mcp.core import Fault
from conftest import prepare


@pytest.fixture
def shell_transport(tmp_path, monkeypatch):
    profile = tmp_path / 'profile.sh'
    profile.write_text('false\nexport PROFILE_READY=yes\n')
    monkeypatch.setattr(slurm, 'SYSTEM_PROFILE', str(profile))
    actual_run = subprocess.run

    def run(args, **kwargs):
        if args[0] == 'ssh':
            args = ['bash', '--noprofile', '--norc', '-s']
        return actual_run(args, **kwargs)

    monkeypatch.setattr(slurm.subprocess, 'run', run)
    return profile


@pytest.mark.parametrize('source_command', ['source', '.'])
def test_capture_loads_optional_profile_probes_without_invoking_engine(tmp_path, shell_transport, source_command):
    engine = tmp_path / 'engine'
    invoked = tmp_path / 'invoked'
    engine.write_text('#!/bin/sh\ntouch ' + shlex.quote(str(invoked)) + '\n')
    engine.chmod(0o755)
    script = source_command + ' ' + shlex.quote(str(shell_transport))
    script += '\n[ "$PROFILE_READY" = yes ]\nexport PATH=' + shlex.quote(str(Path(sys.executable).parent)) + ':"$PATH"'
    result = slurm.Slurm(None).inspect_runtime(dict(ssh_alias='fixture', environment_script=script), [str(engine)], [])
    assert result['identity']['commands'][str(engine)]['bytes'] == engine.stat().st_size
    assert not invoked.exists()


def test_capture_preserves_module_failure_stage_and_exit_status(shell_transport):
    config = dict(ssh_alias='fixture', environment_script='module() { return 17; }\nmodule load missing\nprintf unreachable')
    with pytest.raises(Fault) as caught:
        slurm.Slurm(None).inspect_runtime(config, ['missing-engine'], [])
    error = caught.value.as_dict()
    assert error['evidence']['exit_code'] == 17
    assert error['evidence']['phase'] == 'configured_environment'
    assert error['evidence']['engine_invoked'] is False
    assert 'unreachable' not in error['evidence']['stdout']


def test_failed_profile_does_not_reach_next_stage(shell_transport):
    shell_transport.write_text('return 23\n')
    with pytest.raises(Fault) as caught:
        slurm.SSH().command('fixture', slurm.environment_setup('printf unreachable', 'runtime_identity'))
    assert caught.value.evidence['exit_code'] == 23
    assert caught.value.evidence['phase'] == 'system_profile'
    assert 'unreachable' not in caught.value.evidence['stdout']


def test_silent_remote_exit_still_has_actionable_receipt(shell_transport):
    with pytest.raises(Fault) as caught:
        slurm.SSH().command('fixture', 'exit 19')
    assert caught.value.evidence['exit_code'] == 19
    assert '19' in str(caught.value)
    assert caught.value.evidence['stdout'] == caught.value.evidence['stderr'] == ''


@pytest.mark.parametrize('setup,expected', [('export CONFIG_READY=yes', 0), ('false\nexport CONFIG_READY=yes', 1)])
def test_actual_staged_job_uses_same_profile_rules(service, worker, task, tmp_path, shell_transport, setup, expected):
    prepared = prepare(service, worker, task)
    with service.store.connect() as db:
        run = dict(db.execute('SELECT * FROM runs WHERE id=?', (prepared['id'],)).fetchone())
    environment = json.loads(run['environment'])
    environment['environment_script'] = setup
    run['environment'] = json.dumps(environment)
    destination = tmp_path / 'remote'
    run['remote_dir'] = str(destination)

    class Transfer:
        def command(self, *args, **kwargs):
            return ''

        def transfer(self, alias, local, remote, upload):
            shutil.copytree(local, remote)
            (Path(remote) / 'run.sh').write_text('[ "$PROFILE_READY" = yes ] && [ "$CONFIG_READY" = yes ]\nprintf native-entry-started\n')

    slurm.Slurm(service.store, Transfer()).stage(run)
    result = subprocess.run(['bash', str(destination / '.alm-job.sh')], capture_output=True, text=True)
    assert result.returncode == expected
    assert ('native-entry-started' in result.stdout) == (expected == 0)
    if expected:
        assert 'phase=configured_environment' in result.stderr
