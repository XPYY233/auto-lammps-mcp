"""P3 stage: use service/worker transactions and a controllable scheduler."""
import json

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import sys

from auto_lammps_mcp.core import Fault, encode
from auto_lammps_mcp.service import Service
from auto_lammps_mcp.worker import Worker
from conftest import revision
from test_validation_reuse import qualify
from test_process_integration import start, stop


def prepare_batch(service, task, sid, spec, tag='batch', count=2, seed=100):
    args = dict(task_id=task['id'], revision=revision(service, task), plan_id=spec['plan_id'],
                snapshot_id=sid, samples=[dict(sample_id=f'{tag}-{i}', seed=seed+i) for i in range(count)], op_id=tag)
    batch = service.batch_prepare(**args)
    assert service.batch_prepare(**args) == batch
    return batch['batch_id']


def test_twenty_members_finish_once_with_cost_and_durable_summary(service, task, worker, scheduler):
    sid, spec = qualify(service, worker, scheduler, task)
    bid = prepare_batch(service, task, sid, spec, count=20)
    args = dict(batch_id=bid, revision=revision(service, task), op_id='batch-submit')
    first = service.batch_submit(**args)
    assert service.batch_submit(**args) == first
    for _ in range(20):
        worker.dispatch_one()
        worker.dispatch_one()  # queued member holds the cluster slot
        scheduler.state = 'completed'
        worker.poll()
    result = service.batch_get(bid)
    assert scheduler.submissions == 21  # one qualification plus twenty production jobs
    assert result['progress'] == dict(total=20, completed=20, active=0, waiting=0, failed=0, cancelled=0, remaining=0)
    assert result['accounted_core_hours'] == pytest.approx(20 * 320 / 3600)
    assert result['accounting_final'] and result['execution_state'] == 'completed'
    assert '核验' in result['next_action']
    with service.store.connect() as db:
        refs = db.execute('SELECT stage,coverage FROM run_validations WHERE run_id IN (SELECT run_id FROM batch_members WHERE batch_id=?)', (bid,)).fetchall()
    assert len(refs) == 20 and all(row['stage'] == 'production' for row in refs)
    worker.batch_observations()
    restored = Service(service.store.home)
    Worker(restored, scheduler).batch_observations()
    with service.store.connect() as db:
        assert db.execute("SELECT count(*) FROM events WHERE kind='batch_execution_finished' AND task_id=?", (task['id'],)).fetchone()[0] == 1
    assert restored.task_get(task['id'])['batches']['items'][0]['batch_id'] == bid
    service.batch_submit(bid, revision(service, task), 'submit-again')
    worker.dispatch_one()
    assert scheduler.submissions == 21


def test_pause_during_upload_and_task_pause_dominate_batch_resume(service, task, worker, scheduler):
    sid, spec = qualify(service, worker, scheduler, task)
    bid = prepare_batch(service, task, sid, spec)
    service.batch_submit(bid, revision(service, task), 'submit-batch')
    stage = scheduler.stage
    def pause(run):
        stage(run)
        service.batch_control(bid, revision(service, task), 'pause', 'upload-pause')
    scheduler.stage = pause
    worker.dispatch_one()
    assert scheduler.submissions == 1
    scheduler.stage = stage
    service.task_control(task['id'], revision(service, task), 'pause', 'task-pause')
    service.batch_control(bid, revision(service, task), 'resume', 'batch-resume')
    worker.dispatch_one()
    assert scheduler.submissions == 1 and service.batch_get(bid)['effective_control'] == 'paused'
    service.task_control(task['id'], revision(service, task), 'resume', 'task-resume')
    worker.dispatch_one()
    assert scheduler.submissions == 2


def test_stop_inflight_lost_reply_survives_restart_and_other_batch_continues(service, task, worker, scheduler):
    sid, spec = qualify(service, worker, scheduler, task)
    first = prepare_batch(service, task, sid, spec, 'first')
    second = prepare_batch(service, task, sid, spec, 'second', seed=200)
    for bid in (first, second):
        service.batch_submit(bid, revision(service, task), 'submit-' + bid)
    submit = scheduler.submit
    def lost(run):
        submit(run)
        service.batch_control(first, revision(service, task), 'stop', 'stop-inflight')
        raise TimeoutError('receipt lost after stop')
    scheduler.submit = lost
    worker.dispatch_one()
    one = service.batch_get(first)['members'][0]
    assert one['state'] == 'unknown' and one['evidence']['cancel_requested']
    assert 'preparation' in service.run_get(one['run_id'])['evidence']
    assert service.batch_get(first)['counts'] == {'unknown': 1, 'cancelled_before_dispatch': 1}
    scheduler.submit = submit
    scheduler.state = 'running'
    resumed = Worker(Service(service.store.home), scheduler)
    resumed.dispatch_one()
    assert scheduler.submissions == 2
    resumed.poll()
    resumed.work_one()
    assert one['run_id'] in scheduler.cancelled
    scheduler.state = 'cancelled'
    resumed.poll()
    resumed.dispatch_one()
    assert scheduler.submissions == 3
    assert service.batch_get(second)['counts'] == {'queued': 1, 'pending': 1}
    assert service.batch_get(first)['execution_state'] == 'cancelled'


def test_compact_output_unknown_cost_disconnect_and_five_minute_summary(service, task, worker, scheduler):
    sid, spec = qualify(service, worker, scheduler, task)
    bid = prepare_batch(service, task, sid, spec)
    service.batch_submit(bid, revision(service, task), 'submit-batch')
    worker.dispatch_one()
    result = service.batch_get(bid)
    assert len(result['accounting_unknown_runs']) == 1 and not result['accounting_final']
    rid = result['members'][0]['run_id']
    original_query = scheduler.query
    scheduler.query = lambda cluster, runs: {r['job_id']: dict(state='running', output_progress=dict(files=[dict(path='log.lammps', bytes=400, thermo=dict(Step=100, Time=.05), text='X' * 20000)])) for r in runs}
    worker.poll()
    result = service.batch_get(bid)
    observed = result['latest_evidence_at']
    assert result['members'][0]['output_progress'][0]['thermo'] == dict(Step=100, Time=.05)
    assert len(encode(result)) < 5000
    assert 'X' * 20000 in encode(service.batch_get(bid, include_details=True))
    assert result['accounting_unknown_runs'] == [rid]
    scheduler.query = original_query
    scheduler.fail_query = True
    worker.poll(); worker.poll()
    result = service.batch_get(bid)
    assert result['disconnected_members'] == 1 and result['latest_evidence_at'] == observed
    worker.tick()
    with service.store.connect() as db:
        data = json.loads(db.execute("SELECT data FROM events WHERE kind='progress_summary' AND task_id=? ORDER BY seq DESC", (task['id'],)).fetchone()[0])
    assert data['batches'][0]['batch_id'] == bid
    assert data['batches'][0]['progress']['waiting'] == 1


def test_shared_defect_pauses_only_matching_batch(service, task, worker, scheduler):
    sid, spec = qualify(service, worker, scheduler, task)
    first = prepare_batch(service, task, sid, spec, 'first')
    other_plan = service.validation_plan(task['id'], revision(service, task), sid, 'run.sh', spec['specification']['components'], 'other-plan', variations=spec['specification']['variations'], reuse_from=spec['plan_id'])
    # Independent receipts, so this batch does not rely on the suspended plan.
    with service.store.connect() as db:
        rid = db.execute("SELECT run_id FROM run_validations WHERE plan_id=? AND stage='preflight'", (spec['plan_id'],)).fetchone()[0]
    for component in other_plan['specification']['components']:
        op = service.validation_check(task['id'], revision(service, task), other_plan['plan_id'], component['id'], 'independent-' + component['id'], run_id=rid, data_root='' if component['scope'] == 'static' else 'runs/' + rid)
        worker.work_one()
        assert service.operation_get(op['operation_id'])['result']['passed']
    second = prepare_batch(service, task, sid, other_plan, 'second', seed=200)
    for bid in (first, second):
        service.batch_submit(bid, revision(service, task), 'submit-' + bid)
    service.validation_control(task['id'], revision(service, task), spec['plan_id'], 'shared numerical method issue', 'suspend')
    assert service.batch_get(first)['state'] == 'paused'
    assert service.batch_get(second)['state'] == 'active'
    worker.dispatch_one()
    assert scheduler.submissions == 2 and service.batch_get(second)['counts']['queued'] == 1


@pytest.mark.asyncio
async def test_mcp_discovers_same_batch_after_daemon_restart(service, task, worker, scheduler):
    sid, spec = qualify(service, worker, scheduler, task)
    first = prepare_batch(service, task, sid, spec, 'first')
    second = prepare_batch(service, task, sid, spec, 'second', seed=200)
    service.task_control(task['id'], revision(service, task), 'pause', 'hold')
    for _ in range(2):
        process = start(service.store.home)
        try:
            params = StdioServerParameters(command=sys.executable, args=['-m', 'auto_lammps_mcp.cli', '--home', str(service.store.home), 'mcp', '--host', 'p3-stage'])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    assert next(t for t in tools.tools if t.name == 'batch_list').annotations.readOnlyHint
                    result = await client.call_tool('batch_list', dict(task_id=task['id'], limit=1))
                    assert not result.isError
                    page = json.loads(result.content[0].text)
                    assert page['items'][0]['batch_id'] == second and page['more']
                    result = await client.call_tool('batch_list', dict(task_id=task['id'], offset=page['next_offset'], limit=1))
                    assert json.loads(result.content[0].text)['items'][0]['batch_id'] == first
                    result = await client.call_tool('batch_get', dict(batch_id=first))
                    view = json.loads(result.content[0].text)
                    assert view['effective_control'] == 'paused' and view['progress']['waiting'] == 2
        finally:
            stop(process)
    assert scheduler.submissions == 1
    with pytest.raises(Fault):
        service.batch_list(task['id'], limit=0)
