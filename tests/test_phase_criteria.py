import copy
import json
import subprocess
import sys

import pytest

from auto_lammps_mcp import node_guard
from auto_lammps_mcp.node_guard import Watch
from auto_lammps_mcp.verification import execute_check, run_checks
from conftest import revision
from test_validation_reuse import qualify


LOG = '''units metal
fix eq all nvt temp 300 300 .1
run 100
Step Time TotEng
0 0 -100
100 .1 -90
Loop time
unfix eq
fix collide all nve
timestep .00005
run 100
Step Time TotEng
100 .1 -90
200 .105 -80
Loop time
timestep .0001
run 100
Step Time TotEng
200 .105 -80
300 .115 -79
Loop time
unfix collide
fix cool all nvt temp 300 300 .1
run 100
Step Time TotEng
300 .115 -79
400 .215 -100
Loop time
'''


def test_actual_phase_selects_first_nve_and_excludes_cooling(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / 'log.lammps'
    path.write_text(LOG)
    spec = dict(kind='thermo', path='log.lammps', phase={'fix_id': 'collide', 'style': 'nve'}, max_abs_drift=2)
    measured, passed = execute_check(path, spec)
    assert not passed and measured['checked_blocks'] == 2
    assert measured['blocks'][0]['max_abs_drift'] == 10
    assert measured['blocks'][1]['max_abs_drift'] == 11  # no reset at changed dt
    watch = Watch(spec)
    assert watch.read()['measured'] == 11 and watch.coverage == [1, 2]


def test_bare_block_cooling_is_reported_as_wrong_applicability(tmp_path):
    path = tmp_path / 'log.lammps'
    path.write_text(LOG)
    measured, passed = execute_check(path, dict(kind='thermo', path=str(path), block=3, max_abs_drift=2))
    assert not passed and measured['blocks'][0]['applicability']['reason'] == 'energy_exchange'


def test_bad_unselected_output_does_not_discard_selected_measurement(tmp_path):
    path = tmp_path / 'log.lammps'
    path.write_text(LOG.replace('400 .215 -100', '400 .215'))
    measured, passed = execute_check(path, dict(kind='thermo', path=str(path), phase={'fix_id': 'collide'}, max_abs_drift=12))
    assert passed and measured['checked_blocks'] == 2


def test_different_integrator_phase_uses_its_own_reference(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / 'log.lammps'
    path.write_text(LOG + 'unfix cool\nfix final all nve\nrun 1\nStep Time TotEng\n400 .215 -100\n401 .216 -100\n')
    spec = dict(kind='thermo', path=str(path), phase={'style': 'nve'}, max_abs_drift=12)
    measured, passed = execute_check(path, spec)
    assert passed and measured['blocks'][-1]['max_abs_drift'] == 0
    assert Watch(spec).read() is None


@pytest.mark.parametrize('role', ['required', 'candidate', 'advisory'])
def test_warning_does_not_abort_and_keeps_measurement(tmp_path, role):
    spec = dict(kind='thermo', path='log.lammps', phase={'fix_id': 'collide'}, max_abs_drift=2, role=role)
    (tmp_path / 'spec.json').write_text(json.dumps({'checks': [spec], 'interval_seconds': .01}))
    (tmp_path / 'recorded.log').write_text(LOG)
    (tmp_path / 'entry.sh').write_text('cp recorded.log log.lammps\nsleep .05\necho finished > result.txt\n')
    result = subprocess.run([sys.executable, node_guard.__file__, 'spec.json', 'entry.sh'], cwd=tmp_path, timeout=5)
    assert result.returncode == 0 and (tmp_path / 'result.txt').exists()
    record = json.loads((tmp_path / '.alm-guard.json').read_text())
    assert record['checks'][0]['issue']['measured'] == 11


def test_warning_and_candidate_failures_do_not_revoke_valid_component(service, task):
    root = service.store.root(task['id'])
    (root / 'metrics.json').write_text('{"drift":20,"temperature":300}')
    original = run_checks(service.store, task['id'], [dict(kind='json', path='metrics.json', bounds={'temperature': {'max': 400}})])
    candidate = run_checks(service.store, task['id'], [dict(kind='json', path='metrics.json', bounds={'drift': {'max': 1}}, role='candidate')])
    assert candidate['candidate_only'] and not candidate['passed']
    assert service.check_get(task['id'], original['id'])['current_passed']
    failed = run_checks(service.store, task['id'], [dict(kind='json', path='metrics.json', bounds={'drift': {'max': 1}})])
    assert not failed['passed'] and service.check_get(task['id'], original['id'])['current_passed']
    mixed = run_checks(service.store, task['id'], [dict(kind='json', path='metrics.json', bounds={'temperature': {'max': 400}}),
        dict(kind='json', path='metrics.json', bounds={'drift': {'max': 1}}, role='advisory')])
    assert mixed['passed'] and mixed['advisory']


def test_monitor_change_and_optional_candidate_preserve_reuse(service, task, worker, scheduler):
    sid, original = qualify(service, worker, scheduler, task)
    components = copy.deepcopy(original['specification']['components'])
    components.append(dict(id='alternative', scope='analysis', files=['in.lmp'], condition_keys=[], required=False,
                           checks=[dict(kind='file', path='not-yet-created', role='candidate')]))
    revised = service.validation_plan(task['id'], revision(service, task), sid, 'run.sh', components, 'new',
        variations=original['specification']['variations'], reuse_sources=[original['plan_id']],
        monitor=[dict(kind='thermo', path='log.lammps', phase={'fix_id': 'collide'}, max_abs_drift=.1, role='advisory')], rationale='compare candidate without discarding existing evidence')
    assert set(revised['reused']) == {'static', 'environment', 'numerics', 'outputs'}
    match = service.validation_match(task['id'], sid, revised['plan_id'])
    assert match['ready'] and match['advisory_missing'] == ['alternative']
    comparison = service.validation_compare(task['id'], [original['plan_id'], revised['plan_id']], sid)
    assert comparison['physics_submissions'] == 0 and len(comparison['plans']) == 2


def test_completed_component_from_failed_job_is_checkable(service, task, worker, scheduler):
    sid, plan = qualify(service, worker, scheduler, task)
    with service.store.transaction() as db:
        run = db.execute('SELECT id FROM runs WHERE task_id=?', (task['id'],)).fetchone()[0]
        db.execute("UPDATE runs SET state='failed' WHERE id=?", (run,))
    operation = service.validation_check(task['id'], revision(service, task), plan['plan_id'], 'environment',
        'reuse-partial-job', run_id=run, data_root='runs/' + run)
    worker.work_one()
    assert service.operation_get(operation['operation_id'])['result']['passed']


def test_qualified_native_checker_reuses_existing_run_in_revised_plan(service, task, worker, scheduler):
    sid, original = qualify(service, worker, scheduler, task)
    root = service.store.root(task['id'])
    script = "import json\nfrom pathlib import Path\nvalues=[]\nfor line in Path('sample.log').read_text().splitlines():\n try:\n  row=[float(x) for x in line.split()]\n  if len(row)==3: values.append(row[-1])\n except ValueError: pass\nPath('metrics.json').write_text(json.dumps({'drift':max(abs(x-values[0]) for x in values)}))\n"
    (root / 'measure.py').write_text(script)
    cases = []
    for index, end in enumerate((-100.1, -80)):
        (root / 'sample.log').write_text(f'Step Time TotEng\n0 0 -100\n1 .1 {end}\n')
        op = service.analysis_submit(task['id'], revision(service, task), 'measure.py', ['sample.log'], ['metrics.json'], 'fixture-' + str(index))
        worker.work_one()
        cases.append(dict(operation_id=op['operation_id'], output='metrics.json', expected_passed=index == 0))
    criteria = dict(kind='json', bounds={'drift': {'max': 1}})
    checker = service.checker_register(task['id'], revision(service, task), 'custom drift', cases, criteria, 'qualify-checker')
    snapshot = service.project_snapshot(task['id'], revision(service, task), 'checker-input', paths=['run.sh', 'in.lmp', 'W.eam', 'measure.py'])
    worker.work_one()
    sid = service.operation_get(snapshot['operation_id'])['result']['snapshot_id']
    components = copy.deepcopy(original['specification']['components'])
    components[2].update(checker_id=checker['checker_id'], checks=[dict(**criteria, path='metrics.json')])
    components[2]['files'].append('measure.py')
    revised = service.validation_plan(task['id'], revision(service, task), sid, 'run.sh', components, 'custom-plan',
        reuse_from=original['plan_id'], variations=original['specification']['variations'])
    assert revised['reused'] == ['static', 'environment', 'outputs']
    with service.store.connect() as db:
        rid = db.execute('SELECT id FROM runs WHERE task_id=?', (task['id'],)).fetchone()[0]
    operation = service.analysis_submit(task['id'], revision(service, task), 'measure.py', [], ['metrics.json'], 'check-old-data',
        input_bindings={'sample.log': 'runs/' + rid + '/log.lammps'})
    worker.work_one()
    measured = service.validation_check(task['id'], revision(service, task), revised['plan_id'], 'numerics', 'native-component',
        run_id=rid, data_root='runs/' + rid, analysis_operation_id=operation['operation_id'])
    worker.work_one()
    assert service.operation_get(measured['operation_id'])['result']['passed']
    assert service.validation_match(task['id'], sid, revised['plan_id'])['ready']
    assert scheduler.submissions == 1
