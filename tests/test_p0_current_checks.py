import pytest

from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.verification import run_checks, validate_checks
from conftest import revision, prepare


def test_failed_recheck_invalidates_earlier_pass_without_reregistration(service, task):
    root = service.store.root(task['id'])
    (root / 'report.txt').write_text('energy drift report')
    (root / 'metrics.json').write_text('{"drift":20}')
    passed = run_checks(service.store, task['id'], [dict(kind='json', path='metrics.json', bounds={'drift': {'max': 30}})], artifacts=['report.txt'])
    service.artifact_register(task['id'], revision(service, task), 'report.txt', ['metrics.json'], ['curve'], [{'receipt_id': passed['id']}], 'register')
    assert service.completion_check(task['id'], revision(service, task), 'first')['complete']
    failed = run_checks(service.store, task['id'], [dict(kind='json', path='metrics.json', bounds={'drift': {'max': 1}})])
    assert not failed['passed']
    assert not service.check_get(task['id'], passed['id'])['current_passed']
    assert not service.completion_check(task['id'], revision(service, task), 'rechecked')['complete']
    assert service.task_get(task['id'])['state'] == 'active'
    run_checks(service.store, task['id'], [dict(kind='file', path='metrics.json')])
    assert not service.check_get(task['id'], passed['id'])['current_passed']


def test_failed_recheck_keeps_pause_and_unrelated_result(service, task):
    root = service.store.root(task['id'])
    (root / 'metrics.json').write_text('{"drift":20}')
    (root / 'other.txt').write_text('unrelated result')
    unrelated = run_checks(service.store, task['id'], [dict(kind='file', path='other.txt')])
    first = run_checks(service.store, task['id'], [dict(kind='json', path='metrics.json', bounds={'drift': {'max': 30}})])
    service.artifact_register(task['id'], revision(service, task), 'metrics.json', [], ['curve'], [{'receipt_id': first['id']}], 'register')
    service.task_control(task['id'], revision(service, task), 'pause', 'pause')
    run_checks(service.store, task['id'], [dict(kind='json', path='metrics.json', bounds={'drift': {'max': 1}})])
    assert service.task_get(task['id'])['state'] == 'paused'
    assert service.check_get(task['id'], unrelated['id'])['current_passed']
    (root / 'metrics.json').write_text('{"drift":0.1}')
    fixed = run_checks(service.store, task['id'], [dict(kind='json', path='metrics.json', bounds={'drift': {'max': 1}})])
    assert service.check_get(task['id'], fixed['id'])['current_passed']
    assert not service.check_get(task['id'], first['id'])['current_passed']


@pytest.mark.parametrize('kind', ['json', 'csv'])
@pytest.mark.parametrize('bounds', [{}, {'maximum': 1}, {'min': 2, 'max': 1}, {'max': float('inf')}, {'max': True}])
def test_invalid_measurement_criteria_rejected_before_queue(kind, bounds):
    with pytest.raises(Fault):
        validate_checks([dict(kind=kind, path='data', bounds={'drift': bounds})])


def test_truncated_numeric_thermo_row_does_not_pass_from_earlier_samples(service, task):
    root = service.store.root(task['id'])
    (root / 'log.lammps').write_text('Step Time TotEng\n0 0 -100\n1 .1 -100\n2 .2\n')
    receipt = run_checks(service.store, task['id'], [dict(kind='thermo', path='log.lammps', min_rows=2, max_abs_drift=1)])
    assert not receipt['passed']


def test_pause_holds_queued_analysis_but_allows_diagnostic_checks(service, task, worker):
    root = service.store.root(task['id'])
    (root / 'data.txt').write_text('existing data')
    (root / 'analysis.py').write_text("from pathlib import Path\nPath('result.txt').write_text('result')\n")
    analysis = service.analysis_submit(task['id'], revision(service, task), 'analysis.py', ['data.txt'], ['result.txt'], 'analysis')
    service.task_control(task['id'], revision(service, task), 'pause', 'pause')
    check = service.check_run(task['id'], revision(service, task), [dict(kind='file', path='data.txt')], 'diagnostic')
    worker.work_one()
    assert service.operation_get(analysis['operation_id'])['state'] == 'pending'
    assert service.operation_get(check['operation_id'])['state'] == 'completed'
    service.task_control(task['id'], revision(service, task), 'resume', 'resume')
    worker.work_one()
    assert service.operation_get(analysis['operation_id'])['state'] == 'completed'


def test_pause_during_upload_and_completion_check_never_resumes_dispatch(service, task, worker, scheduler, monkeypatch):
    run = prepare(service, worker, task)
    service.run_submit(run['id'], revision(service, task), 'submit')
    monkeypatch.setattr(scheduler, 'stage', lambda run: service.task_control(task['id'], revision(service, task), 'pause', 'pause-upload'))
    worker.dispatch_one()
    service.completion_check(task['id'], revision(service, task), 'inspect-completion')
    worker.dispatch_one()
    assert service.task_get(task['id'])['state'] == 'paused'
    assert scheduler.submissions == 0
