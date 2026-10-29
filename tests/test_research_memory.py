import json
import sqlite3

import pytest

from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.service import Service
from auto_lammps_mcp.verification import run_checks
from conftest import revision


def test_memory_shared_restart_aliases_correction_and_scope(service, task):
    saved = service.memory_record(task['id'], 1, 'Tungsten cascade timestep', 'short range potential', 'failure', 'remember', conditions={'temperature': 300})
    other = service.task_create('钨级联', '借鉴过去工作', [{'id': 'a', 'description': 'result'}], 'other')
    reopened = Service(service.store.home)
    items = reopened.memory_search(other['id'], '钨 时间步')['items']
    assert saved['memory']['id'] in {x['id'] for x in items}
    assert next(x for x in items if x['id'] == saved['memory']['id'])['status'] == 'candidate'
    assert reopened.task_get(other['id'])['research_context']['memory']['items']
    reopened.memory_record(task['id'], revision(service, task), '', '', 'failure', 'disable', memory_id=saved['memory']['id'], action='disable', reason='disproved by new evidence')
    assert not reopened.memory_search(other['id'], 'timestep')['items']
    assert len(reopened.memory_get(task['id'], saved['memory']['id'])['history']) == 2
    other = service.task_create('Independent', 'isolated evaluation', [{'id': 'a', 'description': 'result'}], 'isolated', mode='formal_b')
    with pytest.raises(Fault):
        service.memory_get(other['id'], saved['memory']['id'])


def test_caller_verdict_never_completes_requirement(service, task):
    root = service.store.root(task['id'])
    (root / 'answer.txt').write_text('claim')
    (root / 'check.json').write_text('{"passed":true}')
    result = service.artifact_register(task['id'], 1, 'answer.txt', [], ['curve'], [dict(passed=True, method='claim', evidence='check.json')], 'claim')
    assert not service.completion_check(task['id'], result['revision'], 'complete')['complete']
    assert service.task_get(task['id'], True)['artifacts'][0]['checks'][0]['claimed_passed']


def test_energy_blocks_injection_and_drift_measured_not_claimed(service, task, worker):
    root = service.store.root(task['id'])
    (root / 'log.lammps').write_text('Step Time TotEng\n0 0 -100\n1 .1 -100\nLoop time\nStep Time TotEng\n1 .1 9900\n2 .2 9920\nLoop time\n')
    checks = [dict(kind='energy_jump', path='log.lammps', before_block=0, after_block=1, expected=10000, tolerance=.01),
              dict(kind='thermo', path='log.lammps', block=1, max_abs_drift=1, time_column='Time')]
    op = service.check_run(task['id'], 1, checks, 'check')
    worker.work_one()
    result = service.operation_get(op['operation_id'])['result']
    assert not result['passed']
    assert result['checks'][0]['measured']['delta'] == 10000
    assert result['checks'][1]['measured']['blocks'][0]['max_abs_drift'] == 20
    assert service.check_get(task['id'], result['id'])['checks'][1]['specification']['max_abs_drift'] == 1
    assert run_checks(service.store, task['id'], checks, operation_id=op['operation_id'])['id'] == result['id']


def test_all_trajectory_frames_checked(service, task):
    root = service.store.root(task['id'])
    def frame(step, atom):
        return f'ITEM: TIMESTEP\n{step}\nITEM: NUMBER OF ATOMS\n1\nITEM: BOX BOUNDS pp pp pp\n0 10\n0 10\n0 10\nITEM: ATOMS id element x y z\n{atom} W 0 0 0\n'
    (root / 'traj.dump').write_text(frame(0, 1) + frame(1, 2))
    result = run_checks(service.store, task['id'], [dict(kind='trajectory', path='traj.dump', min_frames=2)])
    assert not result['passed'] and result['checks'][0]['measured']['checked_frames'] == 2
    (root / 'traj.dump').write_text(frame(0, 1) + frame(1, 1))
    fixed = run_checks(service.store, task['id'], [dict(kind='trajectory', path='traj.dump', min_frames=2)])
    assert fixed['passed']
    (root / 'traj.dump').write_text(frame(0, 1))
    assert not service.check_get(task['id'], fixed['id'])['current_passed']


def test_schema_migration_preserves_history_and_backup(service, task):
    with service.store.connect() as db:
        db.execute('PRAGMA user_version=1')
    reopened = Service(service.store.home)
    assert reopened.task_get(task['id'])['request'] == task['request']
    backups = list((service.store.home / 'migrations').glob('*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
    with reopened.store.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
