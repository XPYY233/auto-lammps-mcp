import pytest
from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.service import Service
from auto_lammps_mcp.worker import Worker


def test_save_intent_while_worker_owns_task_and_resume_once_after_restart(service, task):
    service.setup_configure('background', {'enabled': True}, 'enable')
    with service.store.transaction() as db:
        export = service.store.queue(db, task['id'], 'export', {})['operation_id']
    lease = service.task_claim(task['id'], 'worker:' + export)
    args = dict(task_id=task['id'], revision=1, instruction='verify saved archive', op_id='continue', after_operations=[export])
    saved = service.background_request(**args)
    assert service.background_request(**args) == saved
    assert service.task_get(task['id'])['executor']['owner'] == 'worker:' + export
    assert saved['waiting_for'] == [export]
    service.task_release(task['id'], lease['token'])
    reopened = Service(service.store.home)
    worker = Worker(reopened)
    completed = []
    worker.background = lambda work, payload: completed.append(payload) or {'done': True}
    worker.work_one(decisions=True)
    assert completed == []
    assert reopened.task_get(task['id'])['waiting_background'] == [dict(operation_id=saved['operation_id'], after_operations=[export])]
    with reopened.store.transaction() as db:
        db.execute("UPDATE queue SET state='completed' WHERE id=?", (export,))
    worker.work_one(decisions=True)
    worker.work_one(decisions=True)
    assert len(completed) == 1
    assert reopened.operation_get(saved['operation_id'])['state'] == 'completed'


def test_failed_dependency_waits_for_user_resume_and_task_background_setting(service, task, worker):
    service.setup_configure('background', {'enabled': True}, 'enable')
    with service.store.transaction() as db:
        op = service.store.queue(db, task['id'], 'analysis', {})['operation_id']
        db.execute("UPDATE queue SET state='failed' WHERE id=?", (op,))
    saved = service.background_request(task['id'], 1, 'diagnose failure', 'continue', after_operations=[op])
    service.task_control(task['id'], 2, 'pause', 'pause')
    worker.background = lambda work, payload: {'diagnosis': 'read retained failure'}
    worker.work_one(decisions=True)
    assert service.operation_get(saved['operation_id'])['state'] == 'pending'
    service.task_background(task['id'], 3, False, 'hold')
    service.task_control(task['id'], 4, 'resume', 'resume')
    worker.work_one(decisions=True)
    assert service.operation_get(saved['operation_id'])['state'] == 'pending'
    service.task_background(task['id'], 5, True, 'enable-task')
    worker.work_one(decisions=True)
    assert service.operation_get(saved['operation_id'])['state'] == 'completed'


def test_reject_stale_or_cross_task_dependencies_without_changing_revision(service, task):
    other = service.task_create('other', 'separate task', [{'id': 'x', 'description': 'result'}], 'other')
    with service.store.transaction() as db:
        op = service.store.queue(db, other['id'], 'export', {})['operation_id']
    for args in [dict(revision=0), dict(revision=1, after_operations=[op]), dict(revision=1, after_operations=['missing'])]:
        with pytest.raises(Fault):
            service.background_request(task['id'], instruction='continue', op_id=str(args), **args)
    assert service.task_get(task['id'])['revision'] == 1
    assert service.task_get(task['id'])['work'] == []
