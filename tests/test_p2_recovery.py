import json
import subprocess
import sys

import pytest

from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.runtime_probe import fingerprint, program_identity
from conftest import revision
from test_validation_reuse import plan, qualify


def prepare(service, task, sid, spec, tag, **options):
    return service.run_prepare(task['id'], revision(service, task), sid, 'run.sh', tag,
        '300K', 'same-sample', tag, validation_id=spec['plan_id'], **options)


def test_login_system_libraries_do_not_invalidate_compute_evidence(service, worker, scheduler, task):
    sid, spec = qualify(service, worker, scheduler, task)
    original = scheduler.inspect_runtime
    def different_login(*args):
        measured = original(*args)
        measured['identity']['libraries'] = {'/lib64/libc.so': {'sha256': 'different-login'}}
        measured['fingerprint'] = fingerprint(measured['identity'])
        measured['host'] = 'other-login'
        return measured
    scheduler.inspect_runtime = different_login
    op = service.environment_capture(task['id'], revision(service, task), ['lmp'], 'other-login')
    worker.work_one()
    assert service.operation_get(op['operation_id'])['state'] == 'completed'
    assert service.validation_match(task['id'], sid, spec['plan_id'])['ready']


def test_compute_library_change_invalidates_only_physical_components(service, worker, scheduler, task):
    from auto_lammps_mcp.runtime_evidence import remember
    sid, spec = qualify(service, worker, scheduler, task)
    runtime = spec['specification']['runtime']
    measured = json.loads(json.dumps({k: runtime[k] for k in ('identity', 'fingerprint')}))
    measured['identity']['libraries'] = {'/lib64/libc.so': {'sha256': 'new-compute'}}
    measured.update(role='compute', fingerprint=fingerprint(measured['identity']))
    remember(service.store, task['id'], runtime['selection'], measured)
    match = service.validation_match(task['id'], sid, spec['plan_id'])
    assert match['missing'] == ['environment', 'numerics', 'outputs']


def test_compute_guard_records_real_library_diff_before_entry(tmp_path):
    from auto_lammps_mcp import runtime_probe
    program = tmp_path / 'engine'
    program.write_text('#!/bin/sh\nexit 0\n')
    program.chmod(0o755)
    spec = dict(commands=[str(program)], files=[], role='compute')
    measured = runtime_probe.inspect(spec)
    old = json.loads(json.dumps(measured['identity']))
    old['libraries']['/lib64/libc.so'] = {'sha256': 'old'}
    spec.update(programs=program_identity(measured['identity']), expected=fingerprint(old), expected_identity=old)
    path = tmp_path / 'spec.json'
    path.write_text(json.dumps(spec))
    receipt = tmp_path / 'receipt.json'
    result = subprocess.run([sys.executable, runtime_probe.__file__, str(path), str(receipt)])
    assert result.returncode == 78
    assert json.loads(receipt.read_text())['differences'] == ['libraries:/lib64/libc.so']


def test_budget_estimate_does_not_authorize_or_default_to_100(service, worker, task):
    sid, spec = plan(service, worker, task)
    with service.store.transaction() as db:
        policy = service.store.task(db, task['id'])['policy']
        policy.pop('preflight_core_hours')
        policy.pop('preflight_budget_authorization')
        db.execute('UPDATE tasks SET policy=?,constraints_json=? WHERE id=?',
            (json.dumps(policy), json.dumps({'budget_core_hours': 20}), task['id']))
    with pytest.raises(Fault, match='确认的预验证预算'):
        service.validation_plan(task['id'], revision(service, task), sid, 'run.sh',
            spec['specification']['components'], 'unconfirmed-plan', runtime_id=spec['specification']['runtime']['id'])


def test_replan_cannot_enlarge_confirmed_budget(service, worker, task):
    sid, spec = plan(service, worker, task)
    service.task_update(task['id'], revision(service, task), {'policy': {
        'preflight_core_hours': 20, 'preflight_budget_authorization': 'User confirmed 20 core-hours'}}, 'confirmed cap', 'cap')
    with pytest.raises(Fault, match='更换方案不会扩大预算'):
        service.validation_plan(task['id'], revision(service, task), sid, 'run.sh',
            spec['specification']['components'], 'larger-plan', budget_core_hours=80, reuse_from=spec['plan_id'])
    with pytest.raises(Fault, match='用户确认原话'):
        service.task_update(task['id'], revision(service, task), {'policy': {'preflight_core_hours': 80}}, 'model estimate', 'enlarge')


def test_same_call_same_run_and_current_revision(service, worker, task):
    sid, spec = plan(service, worker, task)
    first = prepare(service, task, sid, spec, 'first', stage='preflight')
    repeated = prepare(service, task, sid, spec, 'repeat', stage='preflight')
    assert repeated['id'] == first['id'] and repeated['reused']
    assert repeated['revision'] == revision(service, task)


def test_new_plan_explicitly_replaces_unsubmitted_record(service, worker, task):
    sid, spec = plan(service, worker, task)
    first = prepare(service, task, sid, spec, 'first', stage='preflight')
    new = service.validation_plan(task['id'], revision(service, task), sid, 'run.sh',
        spec['specification']['components'], 'revised-plan', reuse_from=spec['plan_id'])
    with pytest.raises(Fault, match='同一样本'):
        prepare(service, task, sid, new, 'bad-repeat', stage='preflight')
    second = prepare(service, task, sid, new, 'replacement', stage='preflight', supersedes=first['id'])
    assert second['id'] != first['id'] and second['sample_id'] == first['sample_id']
    assert service.run_get(first['id'])['state'] == 'cancelled_before_dispatch'
    assert service.run_get(second['id'])['evidence']['supersedes'] == first['id']
    with service.store.connect() as db:
        assert db.execute('SELECT plan_id FROM run_validations WHERE run_id=?', (second['id'],)).fetchone()[0] == new['plan_id']


def test_stage_is_not_deduplicated_and_unknown_submission_not_replaced(service, worker, scheduler, task):
    sid, spec = qualify(service, worker, scheduler, task)
    preflight = prepare(service, task, sid, spec, 'preflight-again', stage='preflight')
    production = prepare(service, task, sid, spec, 'production')
    assert production['id'] != preflight['id']
    service.run_submit(production['id'], revision(service, task), 'submit-production')
    scheduler.lose_receipt = True
    worker.dispatch_one()
    assert service.run_get(production['id'])['state'] == 'unknown'
    with pytest.raises(Fault, match='未派发'):
        prepare(service, task, sid, spec, 'duplicate-production', supersedes=production['id'])
    assert scheduler.submissions == 2


@pytest.mark.parametrize('qualified', [False, True])
def test_real_stager_uses_compute_evidence_not_login_libraries(service, worker, scheduler, task, monkeypatch, qualified):
    from auto_lammps_mcp.slurm import Slurm
    from pathlib import Path
    sid, spec = qualify(service, worker, scheduler, task) if qualified else plan(service, worker, task)
    run = prepare(service, task, sid, spec, 'stage', stage='production' if qualified else 'preflight')
    slurm = Slurm(service.store)
    measured = scheduler.inspect_runtime({}, ['lmp'], [])
    measured['identity']['libraries'] = {'/lib64/libc.so': {'sha256': 'another-login-host'}}
    measured['fingerprint'] = fingerprint(measured['identity'])
    monkeypatch.setattr(slurm, 'inspect_runtime', lambda *a: measured)
    monkeypatch.setattr(slurm.ssh, 'command', lambda *a, **k: '')
    staged = {}
    def transfer(cluster, root, remote, **kwargs):
        staged.update(json.loads((Path(root) / '.alm-runtime-spec.json').read_text()))
        script = (Path(root) / '.alm-job.sh').read_text()
        assert script.index('.alm-runtime-probe.py') < script.index('bash run.sh')
    monkeypatch.setattr(slurm.ssh, 'transfer', transfer)
    slurm.stage(run)
    assert staged['role'] == 'compute'
    assert staged['programs'] == program_identity(spec['specification']['runtime']['identity'])
    assert staged['expected'] == (spec['specification']['runtime']['fingerprint'] if qualified else None)
    assert service.validation_match(task['id'], sid, spec['plan_id'])['ready'] == qualified


def test_failed_costs_and_reservations_survive_new_plan(service, worker, scheduler, task):
    sid, spec = plan(service, worker, task, budget=1)
    run = prepare(service, task, sid, spec, 'first-budget', stage='preflight')
    service.run_submit(run['id'], run['revision'], 'submit-budget')
    worker.dispatch_one()
    scheduler.state = 'failed'
    worker.poll()  # Fixture records 320 core-seconds, including the failed run.
    revised = service.validation_plan(task['id'], revision(service, task), sid, 'run.sh',
        spec['specification']['components'], 'second-budget-plan', budget_core_hours=.6, reuse_from=spec['plan_id'])
    second = service.run_prepare(task['id'], revision(service, task), sid, 'run.sh', 'next-validation',
        '300K', 'second-sample', 'prepare-next', stage='preflight', validation_id=revised['plan_id'])
    with pytest.raises(Fault, match='预算不足'):
        service.run_submit(second['id'], second['revision'], 'submit-next')
    assert scheduler.submissions == 1
