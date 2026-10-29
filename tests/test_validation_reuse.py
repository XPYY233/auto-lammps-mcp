import json

import pytest

from auto_lammps_mcp.core import Fault
from conftest import revision


def snapshot(service, worker, task, tag):
    op = service.project_snapshot(task['id'], revision(service, task), 'snapshot-' + tag, paths=['run.sh', 'in.lmp', 'W.eam'])
    worker.work_one()
    return service.operation_get(op['operation_id'])['result']['snapshot_id']


def plan(service, worker, task, budget=100):
    service.setup_configure('cluster', dict(ssh_alias='cluster', remote_root='/project/research', account='research', partition='cpu', environment_script='module load engine'), 'cluster')
    root = service.store.root(task['id'])
    (root / 'run.sh').write_text('lmp -in in.lmp\n')
    (root / 'in.lmp').write_text('variable seed index 12345\nunits metal\n')
    (root / 'W.eam').write_text('test potential fixture')
    sid = snapshot(service, worker, task, 'inputs')
    service.task_update(task['id'], revision(service, task), {'constraints': {'temperature': 300}, 'policy': {'authorized': True, 'authorization': 'start fixture', 'wall_seconds': 60}}, 'start fixture', 'authorize')
    components = [
        dict(id='static', scope='static', files=['run.sh', 'in.lmp', 'W.eam'], condition_keys=[], checks=[dict(kind='file', path='run.sh', contains=['-in in.lmp'])]),
        dict(id='environment', scope='environment', files=['run.sh'], condition_keys=[], checks=[dict(kind='file', path='log.lammps', contains=['LAMMPS fixture'])]),
        dict(id='numerics', scope='numerics', files=['in.lmp', 'W.eam'], condition_keys=['temperature'], checks=[dict(kind='thermo', path='log.lammps', max_abs_drift=1, min_rows=2)]),
        dict(id='outputs', scope='outputs', files=['in.lmp'], condition_keys=[], checks=[dict(kind='thermo', path='log.lammps', columns=['Time', 'TotEng'], min_rows=2)]),
    ]
    spec = service.validation_plan(task['id'], revision(service, task), sid, 'run.sh', components, 'plan', variations=[dict(path='in.lmp', variable='seed', min=1, max=100000)], budget_core_hours=budget)
    return sid, spec


def qualify(service, worker, scheduler, task):
    sid, spec = plan(service, worker, task)
    pid = spec['plan_id']
    run = service.run_prepare(task['id'], revision(service, task), sid, 'run.sh', 'preflight', '300K', 'preflight', 'prepare', validation_id=pid, stage='preflight')
    service.run_submit(run['id'], revision(service, task), 'submit')
    worker.dispatch_one()
    scheduler.state = 'completed'
    worker.poll()
    root = service.store.root(task['id']) / 'runs' / run['id']
    root.mkdir(parents=True)
    (root / 'log.lammps').write_text('LAMMPS fixture\nStep Time TotEng\n0 0 -100\n1 .1 -100.1\nLoop time\n')
    worker.work_one()
    for c in spec['specification']['components']:
        op = service.validation_check(task['id'], revision(service, task), pid, c['id'], 'check-' + c['id'], run_id=run['id'], data_root='' if c['scope'] == 'static' else 'runs/' + run['id'])
        worker.work_one()
        result = service.operation_get(op['operation_id'])
        assert result['state'] == 'completed', result
        assert result['result']['passed'], result
    assert service.validation_match(task['id'], sid, pid)['ready']
    return sid, spec


def test_twenty_repeats_reuse_no_preflight_and_pause_restart(service, task, worker, scheduler):
    sid, spec = qualify(service, worker, scheduler, task)
    samples = [dict(sample_id=str(i), seed=1000+i) for i in range(20)]
    args = dict(task_id=task['id'], revision=revision(service, task), plan_id=spec['plan_id'], snapshot_id=sid, samples=samples, op_id='batch')
    batch = service.batch_prepare(**args)
    assert service.batch_prepare(**args) == batch and batch['preflight_jobs'] == 0
    assert len(service.batch_get(batch['batch_id'])['members']) == 20
    service.batch_submit(batch['batch_id'], revision(service, task), 'submit-batch')
    service.batch_control(batch['batch_id'], revision(service, task), 'pause', 'pause')
    worker.dispatch_one()
    assert scheduler.submissions == 1
    service.batch_control(batch['batch_id'], revision(service, task), 'resume', 'resume')
    worker.dispatch_one()
    assert scheduler.submissions == 2
    from auto_lammps_mcp.service import Service
    restored = Service(service.store.home).batch_get(batch['batch_id'])
    assert restored['counts'] == {'queued': 1, 'pending': 19}
    assert len({m['seed'] for m in restored['members']}) == 20


def test_changed_potential_partial_coverage_and_cross_project(service, task, worker, scheduler):
    sid, spec = qualify(service, worker, scheduler, task)
    (service.store.root(task['id']) / 'W.eam').write_text('changed potential')
    changed = snapshot(service, worker, task, 'changed')
    coverage = service.validation_match(task['id'], changed, spec['plan_id'])
    assert coverage['missing'] == ['static', 'numerics'] and coverage['reuse'] == 'partial'
    revised = service.validation_plan(task['id'], revision(service, task), changed, 'run.sh', spec['specification']['components'], 'revised', variations=spec['specification']['variations'], reuse_from=spec['plan_id'])
    assert revised['reused'] == ['environment', 'outputs']
    assert not service.validation_match(task['id'], changed, revised['plan_id'])['ready']


def test_revoked_validation_held_after_staging(service, task, worker, scheduler):
    sid, spec = qualify(service, worker, scheduler, task)
    batch = service.batch_prepare(task['id'], revision(service, task), spec['plan_id'], sid, [dict(sample_id='new', seed=900)], 'batch')
    service.batch_submit(batch['batch_id'], revision(service, task), 'submit-batch')
    memory = next(x for x in service.memory_search(task['id'], limit=50)['items'] if x['kind'] == 'validation')
    def revoke(run):
        service.memory_record(task['id'], revision(service, task), '', '', 'method', 'revoke', memory_id=memory['id'], action='disable')
    scheduler.stage = revoke
    worker.dispatch_one()
    assert scheduler.submissions == 1
    assert service.batch_get(batch['batch_id'])['counts'] == {'prepared': 1}


def test_preflight_budget_counts_reservations_and_failure(service, task, worker, scheduler):
    sid, spec = plan(service, worker, task, budget=.6)
    def prepare(tag):
        return service.run_prepare(task['id'], revision(service, task), sid, 'run.sh', tag, '300K', tag, 'p-'+tag, validation_id=spec['plan_id'], stage='preflight')
    one = prepare('one')
    service.run_submit(one['id'], revision(service, task), 's-one')
    two = prepare('two')
    with pytest.raises(Fault, match='预算不足'):
        service.run_submit(two['id'], revision(service, task), 's-two')
    assert service.run_get(two['id'])['state'] == 'prepared'
    worker.dispatch_one()
    scheduler.state = 'failed'
    worker.poll()
    with pytest.raises(Fault, match='预算不足'):
        service.run_submit(two['id'], revision(service, task), 's-two-again')


def test_source_data_change_invalidates_receipt(service, task, worker, scheduler):
    sid, spec = qualify(service, worker, scheduler, task)
    for path in service.store.root(task['id']).glob('runs/*/log.lammps'):
        path.write_text('replaced')
    match = service.validation_match(task['id'], sid, spec['plan_id'])
    assert match['missing'] == ['environment', 'numerics', 'outputs']


def test_cross_project_match_requires_current_authorization(service, task, worker, scheduler):
    sid, spec = qualify(service, worker, scheduler, task)
    other = service.task_create('Other W cascade', 'repeat validated conditions', [{'id': 'x', 'description': 'result'}], 'other', constraints={'temperature': 300})
    for name in ('run.sh', 'in.lmp', 'W.eam'):
        (service.store.root(other['id']) / name).write_bytes((service.store.root(task['id']) / name).read_bytes())
    copied = snapshot(service, worker, other, 'other')
    assert service.validation_match(other['id'], copied, spec['plan_id'])['ready']
    batch = service.batch_prepare(other['id'], revision(service, other), spec['plan_id'], copied, [dict(sample_id='other', seed=920)], 'other-batch')
    with pytest.raises(Fault, match='授权'):
        service.batch_submit(batch['batch_id'], revision(service, other), 'other-submit')
    assert scheduler.submissions == 1


def test_backup_restores_memory_validation_batch_links(service, task, worker, scheduler, tmp_path):
    from auto_lammps_mcp.backup import create_backup, restore_backup
    from auto_lammps_mcp.service import Service
    sid, spec = qualify(service, worker, scheduler, task)
    batch = service.batch_prepare(task['id'], revision(service, task), spec['plan_id'], sid, [dict(sample_id='sample', seed=777)], 'batch')
    backup = create_backup(service.store, tmp_path / 'backup.zip')
    restore_backup(backup['path'], backup['sha256'], tmp_path / 'restored')
    restored = Service(tmp_path / 'restored')
    assert restored.validation_match(task['id'], sid, spec['plan_id'])['ready']
    assert restored.batch_get(batch['batch_id'])['members'][0]['seed'] == 777
    assert restored.store.setting('recovery')['pending']
    assert restored.memory_search(task['id'])['items']


def test_later_failed_source_check_revokes_cloned_evidence(service, task, worker, scheduler):
    from auto_lammps_mcp.verification import run_checks
    sid, spec = qualify(service, worker, scheduler, task)
    cloned = service.validation_plan(task['id'], revision(service, task), sid, 'run.sh', spec['specification']['components'], 'clone', variations=spec['specification']['variations'], reuse_from=spec['plan_id'])
    assert service.validation_match(task['id'], sid, cloned['plan_id'])['ready']
    run_checks(service.store, task['id'], [dict(kind='file', path='absent.txt')], plan_id=spec['plan_id'], component='numerics')
    assert service.validation_match(task['id'], sid, cloned['plan_id'])['missing'] == ['numerics']


def test_suspend_one_component_preserves_unaffected_reuse(service, task, worker, scheduler):
    sid, spec = qualify(service, worker, scheduler, task)
    service.validation_control(task['id'], revision(service, task), spec['plan_id'], 'numerical method needs a new check', 'suspend', components=['numerics'])
    cloned = service.validation_plan(task['id'], revision(service, task), sid, 'run.sh', spec['specification']['components'], 'clone', variations=spec['specification']['variations'], reuse_from=spec['plan_id'])
    assert set(cloned['reused']) == {'static', 'environment', 'outputs'}
    assert service.validation_match(task['id'], sid, cloned['plan_id'])['missing'] == ['numerics']
