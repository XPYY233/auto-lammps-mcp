"""Read durable batch progress without copying native logs into every reply."""
import json
import math

from .slurm import ACTIVE, TERMINAL

FINISHED = set(TERMINAL) | {'cancelled_before_dispatch', 'staging_failed'}
FAILED = {'failed', 'timeout', 'staging_failed'}


def batch_view(db, batch, *, members=True, details=False):
    task = db.execute('SELECT state,revision FROM tasks WHERE id=?', (batch['task_id'],)).fetchone()
    rows = db.execute('SELECT m.*,r.state,r.snapshot_id,r.job_id,r.evidence,r.updated,r.failures '
                      'FROM batch_members m JOIN runs r ON r.id=m.run_id '
                      'WHERE m.batch_id=? ORDER BY m.ordinal', (batch['id'],)).fetchall()
    counts, items, cost, unknown, confirmed, disconnected = {}, [], 0.0, [], [], 0
    for row in rows:
        item = dict(row)
        state, rid = item['state'], item['run_id']
        counts[state] = counts.get(state, 0) + 1
        evidence = json.loads(item.pop('evidence'))
        measured = evidence.get('accounting', evidence).get('core_seconds')
        if type(measured) in (float, int) and math.isfinite(measured) and measured >= 0:
            cost += measured
        elif item['job_id'] or state in ACTIVE:
            unknown.append(rid)
        observed = evidence.get('confirmed_at')
        if observed is not None:
            confirmed.append(observed)
        connection = 'degraded' if item['failures'] >= 2 else 'observed' if observed else 'unconfirmed'
        disconnected += connection == 'degraded'
        files = [{key: value for key, value in f.items() if key in
                  ('path', 'exists', 'bytes', 'modified_at', 'observed_at', 'thermo')}
                 for f in evidence.get('output_progress', {}).get('files', [])]
        item.update(last_confirmed_at=observed, connection=connection,
                    evidence=evidence if details else {key: evidence[key] for key in
                        ('core_seconds', 'elapsed_seconds', 'exit_code', 'confirmed_at', 'cancel_requested', 'error') if key in evidence},
                    output_progress=files, read=dict(method='run_get', run_id=rid))
        items.append(item)
    total = len(rows)
    finished = sum(n for state, n in counts.items() if state in FINISHED)
    failed = sum(counts.get(state, 0) for state in FAILED)
    active = sum(counts.get(state, 0) for state in ACTIVE)
    waiting = counts.get('prepared', 0) + counts.get('pending', 0)
    cancelled = counts.get('cancelled', 0) + counts.get('cancelled_before_dispatch', 0)
    progress = dict(total=total, completed=counts.get('completed', 0), active=active,
                    waiting=waiting, failed=failed, cancelled=cancelled, remaining=total-finished)
    outcome = 'finished_with_errors' if finished == total and failed else 'cancelled' if finished == total and cancelled else 'completed' if finished == total else 'in_progress'
    control = task['state'] if task['state'] in ('paused', 'stopping', 'stopped') else batch['state']
    next_action = '读取失败成员并保留已完成结果' if outcome == 'finished_with_errors' else '分析和核验已完成输出' if outcome == 'completed' else '核对取消结果并整理已有数据' if control in ('stopped', 'stopping') else '准备继续时恢复任务或批次' if control == 'paused' else 'batch_submit' if batch['state'] == 'prepared' else '等待已提交成员；有未提交成员时读取原因后 batch_submit'
    summary = f"计算结束 {progress['completed']}/{total}，在途 {active}，等待 {waiting}，失败 {failed}，取消 {cancelled}；已观测 {cost / 3600:.3f} 核时"
    if unknown:
        summary += f"，{len(unknown)} 项费用待核对"
    result = dict(id=batch['id'], batch_id=batch['id'], task_id=batch['task_id'], plan_id=batch['plan_id'],
                  state=batch['state'], task_state=task['state'], revision=task['revision'], effective_control=control,
                  execution_state=outcome, counts=counts, progress=progress, summary=summary,
                  accounted_core_hours=cost / 3600, accounting_unknown_runs=unknown,
                  accounting_final=finished == total and not unknown,
                  latest_evidence_at=max(confirmed) if confirmed else None, disconnected_members=disconnected,
                  created=batch['created'], next_action=next_action, read=dict(method='batch_get', batch_id=batch['id']))
    if members:
        result.update(members=items, specification=json.loads(batch['specification']))
    return result
