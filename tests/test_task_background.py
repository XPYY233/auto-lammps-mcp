from auto_lammps_mcp.service import Service
from conftest import revision


def test_task_preference_survives_restart_and_leaves_other_queue_running(service, task, worker, monkeypatch):
    service.setup_configure('background', {'enabled': True}, 'enable-shared')
    other = service.task_create('second', 'independent work', [{'id': 'b', 'description': 'report'}], 'other')
    first_work = service.background_request(task['id'], 1, 'continue first', 'first-background')
    other_work = service.background_request(other['id'], 1, 'continue second', 'other-background')
    service.task_background(task['id'], revision(service, task), False, 'hold-first')
    reopened = Service(service.store.home)
    assert reopened.task_get(task['id'])['automation'] == dict(enabled=False, service_enabled=True, effective_enabled=False)
    assert reopened.task_get(other['id'])['automation']['effective_enabled']
    completed = []
    monkeypatch.setattr(worker, 'background', lambda work, payload: completed.append(work['task_id']) or {'done': True})
    worker.work_one(decisions=True)
    assert completed == [other['id']]
    assert service.operation_get(first_work['operation_id'])['state'] == 'pending'
    assert service.operation_get(other_work['operation_id'])['state'] == 'completed'
    service.task_background(task['id'], revision(service, task), True, 'resume-first')
    worker.work_one(decisions=True)
    assert completed == [other['id'], task['id']]
    assert service.operation_get(first_work['operation_id'])['state'] == 'completed'
    assert service.store.setting('background') == {'enabled': True}


def test_holding_future_turns_preserves_current_lease_work_and_scientific_results(service, task):
    root = service.store.root(task['id'])
    (root / 'result.txt').write_text('existing result')
    (root / 'check.json').write_text('{"passed":true}')
    registered = service.artifact_register(task['id'], 1, 'result.txt', [], ['curve'],
                                          [dict(passed=True, method='read file', evidence='check.json')], 'register')
    operation = service.background_request(task['id'], registered['revision'], 'additional review', 'review')
    with service.store.transaction() as db:
        db.execute("UPDATE queue SET state='running' WHERE id=?", (operation['operation_id'],))
    lease = service.task_claim(task['id'], 'arbitrary-owner')
    args = dict(task_id=task['id'], revision=revision(service, task), enabled=False, op_id='hold', token=lease['token'])
    held = service.task_background(**args)
    assert held == service.task_background(**args)
    assert held['active_background'] == [operation['operation_id']]
    current = service.task_get(task['id'])
    assert current['executor']['owner'] == 'arbitrary-owner'
    assert current['artifacts'][0]['stale'] == 0
    assert current['active_background'][0]['state'] == 'running'
    service.task_release(task['id'], lease['token'])


def test_task_cannot_enable_unconfigured_shared_executor(service, task):
    result = service.task_background(task['id'], 1, True, 'task-only')
    assert result['enabled'] and not result['effective_enabled']
