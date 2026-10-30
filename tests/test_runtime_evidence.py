import json
import subprocess
import sys
from pathlib import Path

import pytest

from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.runtime_probe import inspect, fingerprint
from auto_lammps_mcp.slurm import Slurm
from conftest import revision
from test_validation_reuse import qualify


def changed_measurement(scheduler, config, commands, files):
    value = type(scheduler).inspect_runtime(scheduler, config, commands, files)
    next(iter(value['identity']['commands'].values()))['sha256'] = 'b' * 64
    value['fingerprint'] = fingerprint(value['identity'])
    return value


def test_new_capture_same_bytes_preserves_reuse_changed_bytes_invalidates_only_engine_scopes(service, worker, scheduler, task):
    sid, spec = qualify(service, worker, scheduler, task)
    def capture(tag):
        op = service.environment_capture(task['id'], revision(service, task), ['lmp'], tag)
        worker.work_one()
        return service.operation_get(op['operation_id'])['result']
    capture('same-runtime')
    assert service.validation_match(task['id'], sid, spec['plan_id'])['ready']
    scheduler.inspect_runtime = lambda *args: changed_measurement(scheduler, *args)
    updated = capture('new-runtime')
    match = service.validation_match(task['id'], sid, spec['plan_id'])
    assert match['missing'] == ['environment', 'numerics', 'outputs']
    assert all('runtime_changed' in c['differences'] for c in match['components'] if not c['ready'])
    revised = service.validation_plan(task['id'], revision(service, task), sid, 'run.sh',
        spec['specification']['components'], 'changed-runtime-plan', reuse_from=spec['plan_id'],
        variations=spec['specification']['variations'], runtime_id=updated['id'])
    assert revised['reused'] == ['static']
    assert scheduler.submissions == 1


def test_replaced_engine_blocks_staging_before_submission(service, worker, scheduler, task, monkeypatch):
    sid, spec = qualify(service, worker, scheduler, task)
    run = service.run_prepare(task['id'], revision(service, task), sid, 'run.sh', 'production', '300K', 'new',
                              'production', validation_id=spec['plan_id'])
    service.run_submit(run['id'], revision(service, task), 'submit-production')
    slurm = Slurm(service.store)
    monkeypatch.setattr(slurm, 'inspect_runtime', lambda *args: changed_measurement(scheduler, *args))
    monkeypatch.setattr(slurm, 'submit', lambda run: pytest.fail('changed engine reached sbatch'))
    worker.slurm = slurm
    worker.dispatch_one()
    assert service.run_get(run['id'])['state'] == 'staging_failed'
    assert service.validation_match(task['id'], sid, spec['plan_id'])['missing'] == ['environment', 'numerics', 'outputs']


def test_node_guard_prevents_entry_after_post_staging_replacement(tmp_path):
    from auto_lammps_mcp import runtime_probe
    program = tmp_path / 'scientific-fixture'
    marker = tmp_path / 'engine-was-invoked'
    program.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
    program.chmod(0o755)
    spec = dict(commands=[str(program)], files=[])
    original = inspect(spec)
    assert not marker.exists()  # Capturing identity never invoked the fixture.
    spec['expected'] = original['fingerprint']
    (tmp_path / 'spec.json').write_text(json.dumps(spec))
    program.write_text(program.read_text() + '# replacement at same path\n')
    result = subprocess.run([sys.executable, runtime_probe.__file__, str(tmp_path / 'spec.json'), str(tmp_path / 'receipt.json')])
    assert result.returncode == 78 and not marker.exists()
    assert json.loads((tmp_path / 'receipt.json').read_text())['passed'] is False


def test_shared_library_change_alters_identity(tmp_path, monkeypatch):
    from auto_lammps_mcp import runtime_probe
    binary, library = tmp_path / 'engine', tmp_path / 'libengine.so'
    binary.write_bytes(b'\x7fELFfixture')
    binary.chmod(0o755)
    library.write_bytes(b'library-v1')
    monkeypatch.setattr(runtime_probe.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, f'libengine.so => {library} (0x1234)\n', ''))
    spec = dict(commands=[str(binary)], files=[])
    first = inspect(spec)
    library.write_bytes(b'library-v2')
    second = inspect(spec)
    assert first['identity']['commands'] == second['identity']['commands']
    assert first['fingerprint'] != second['fingerprint']


def test_corrupt_node_identity_cannot_qualify_component(service, worker, scheduler, task):
    sid, spec = qualify(service, worker, scheduler, task)
    runtime_path = next(service.store.root(task['id']).glob('runs/*/.alm-runtime.json'))
    runtime_path.write_text('{}')
    assert service.validation_match(task['id'], sid, spec['plan_id'])['missing'] == ['environment', 'numerics', 'outputs']


def test_missing_runtime_dependency_blocks_reuse_until_readable_again(service, worker, scheduler, task):
    sid, spec = qualify(service, worker, scheduler, task)
    original = scheduler.inspect_runtime
    def missing(*args):
        raise FileNotFoundError('runtime dependency disappeared')
    scheduler.inspect_runtime = missing
    capture = service.environment_capture(task['id'], revision(service, task), ['lmp'], 'missing-dependency')
    worker.work_one()
    assert service.operation_get(capture['operation_id'])['state'] == 'failed'
    assert service.validation_match(task['id'], sid, spec['plan_id'])['missing'] == ['environment', 'numerics', 'outputs']
    scheduler.inspect_runtime = original
    service.environment_capture(task['id'], revision(service, task), ['lmp'], 'restored-dependency')
    worker.work_one()
    assert service.validation_match(task['id'], sid, spec['plan_id'])['ready']


def test_prepared_environment_does_not_follow_new_global_config(service, worker, scheduler, task):
    sid, spec = qualify(service, worker, scheduler, task)
    run = service.run_prepare(task['id'], revision(service, task), sid, 'run.sh', 'production', '300K', 'new',
                              'production', validation_id=spec['plan_id'])
    service.run_submit(run['id'], revision(service, task), 'submit-production')
    config = service.store.setting('cluster')
    service.setup_configure('cluster', {**config, 'environment_script': 'module load changed-engine'}, 'change-config')
    worker.dispatch_one()
    assert service.run_get(run['id'])['state'] == 'prepared' and scheduler.submissions == 1


def test_credential_alias_change_keeps_byte_matched_validation(service, worker, scheduler, task):
    sid, spec = qualify(service, worker, scheduler, task)
    config = service.store.setting('cluster')
    service.setup_configure('cluster', {**config, 'ssh_alias': 'renewed-login'}, 'new-alias')
    assert service.validation_match(task['id'], sid, spec['plan_id'])['ready']


def test_old_unmeasured_environment_plan_is_not_ready(service, worker, scheduler, task):
    sid, spec = qualify(service, worker, scheduler, task)
    legacy = {key: value for key, value in spec['specification'].items() if key != 'runtime'}
    with service.store.transaction() as db:
        db.execute('UPDATE validation_plans SET specification=? WHERE id=?', (json.dumps(legacy), spec['plan_id']))
    assert service.validation_match(task['id'], sid, spec['plan_id'])['missing'] == ['environment', 'numerics', 'outputs']
