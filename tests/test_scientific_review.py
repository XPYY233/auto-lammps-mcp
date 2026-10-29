from conftest import revision
from auto_lammps_mcp.verification import run_checks


def review(service, task, path, passed, tag):
    root = service.store.root(task['id'])
    (root / path).write_text('A conclusion with its measured evidence')
    evidence = tag + '.json'
    (root / evidence).write_text('stable' if passed else 'drift')
    receipt = run_checks(service.store, task['id'], [dict(kind='file', path=evidence, contains=['stable'])], artifacts=[path])
    return service.artifact_register(task['id'], revision(service, task), path, [], ['curve'],
                                     [dict(receipt_id=receipt['id'])], tag)


def test_later_failed_review_reopens_unchanged_report_and_retains_verdicts(service, task):
    review(service, task, 'report.md', True, 'initial')
    assert service.completion_check(task['id'], revision(service, task), 'complete-first')['complete']
    review(service, task, 'report.md', False, 'independent-review')
    assert service.task_get(task['id'])['state'] == 'active'
    check = service.completion_check(task['id'], revision(service, task), 'check-review')
    assert not check['complete'] and [r['id'] for r in check['missing']] == ['curve']
    review(service, task, 'report.md', True, 'corrected-review')
    assert service.completion_check(task['id'], revision(service, task), 'complete-corrected')['complete']
    history = service.task_get(task['id'], include_details=True)['artifacts']
    assert [a['checks'][0]['passed'] for a in history] == [True, False, True]


def test_positive_companion_file_does_not_mask_current_failed_review(service, task):
    review(service, task, 'curve.txt', True, 'curve-check')
    review(service, task, 'report.md', False, 'report-review')
    check = service.completion_check(task['id'], revision(service, task), 'check-all')
    assert not check['complete']
    assert [r['id'] for r in check['missing']] == ['curve']
