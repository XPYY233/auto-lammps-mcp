import json
from pathlib import Path

import pytest

from auto_lammps_mcp.files import file_hash
from auto_lammps_mcp.service import Service
from conftest import revision


def test_failed_analysis_exposes_evidence_and_can_resume(service, task, worker):
    root = service.store.root(task['id'])
    (root / 'data.txt').write_text('1 2 3\n4 5 6\n')
    broken = "from pathlib import Path\nprint('reading three columns', flush=True)\nPath('partial.csv').write_text('first,second,third\\n')\nraise ValueError('expected three columns, read two')\n"
    fixed = "from pathlib import Path\nPath('partial.csv').write_text(Path('data.txt').read_text())\nPath('summary.txt').write_text('two complete rows')\n"
    (root / 'analysis.py').write_text(broken)
    old_hash = file_hash(root / 'analysis.py')
    submitted = service.analysis_submit(task['id'], revision(service, task), 'analysis.py',
                                        ['data.txt'], ['partial.csv', 'summary.txt'], 'broken-analysis')
    worker.work_one()
    reopened = Service(service.store.home)
    failed = reopened.operation_get(submitted['operation_id'])
    assert failed['state'] == 'failed'
    error = failed['result']['error']
    assert error['effect'] == 'partial'
    evidence = error['evidence']
    assert evidence['exit_code'] != 0
    assert evidence['sources']['analysis.py'] == old_hash
    assert evidence['sources']['data.txt'] == file_hash(root / 'data.txt')
    assert evidence['missing_outputs'] == ['summary.txt']
    assert evidence['outputs']['partial.csv']['sha256'] == file_hash(Path(evidence['outputs']['partial.csv']['path']))
    assert 'expected three columns, read two' in reopened.file_read(task['id'], evidence['log_path'])['text']
    persisted = json.loads(reopened.file_read(task['id'], evidence['receipt_path'])['text'])
    assert persisted['status'] == 'failed'
    assert persisted['error']['effect'] == 'partial'
    service.file_patch(task['id'], revision(service, task), 'analysis.py', old_hash, broken, fixed, 'fix-analysis')
    retry = service.analysis_submit(task['id'], revision(service, task), 'analysis.py',
                                    ['data.txt'], ['partial.csv', 'summary.txt'], 'fixed-analysis')
    worker.work_one()
    success = service.operation_get(retry['operation_id'])
    assert success['state'] == 'completed'
    assert Path(success['result']['outputs']['partial.csv']['path']).read_text() == '1 2 3\n4 5 6\n'
    assert (root / 'analysis' / submitted['operation_id'] / 'analysis.py').read_text() == broken
    assert Path(evidence['outputs']['partial.csv']['path']).read_text() == 'first,second,third\n'
    assert reopened.operation_get(submitted['operation_id'])['result'] == failed['result']


def test_task_shows_current_executor_without_its_write_token(service, task, worker, monkeypatch):
    root = service.store.root(task['id'])
    (root / 'analysis.py').write_text('print(1)\n')
    (root / 'data.txt').write_text('1\n')
    operation = service.analysis_submit(task['id'], 1, 'analysis.py', ['data.txt'], ['result.txt'], 'analysis')

    def observe(work, command, *, cwd, env, output, timeout):
        progress = service.task_get(task['id'])
        assert progress['executor']['kind'] == 'worker'
        assert progress['executor']['operation_id'] == operation['operation_id']
        assert progress['executor']['state'] == 'running'
        assert 'token' not in progress['executor']
        (cwd / 'result.txt').write_text('done')
        return 0

    monkeypatch.setattr(worker, 'process', observe)
    worker.work_one()
    assert service.operation_get(operation['operation_id'])['state'] == 'completed'
    assert service.task_get(task['id'])['executor'] is None
    lease = service.task_claim(task['id'], 'user-conversation')
    assert service.task_get(task['id'])['executor']['kind'] == 'agent'
    service.task_release(task['id'], lease['token'])
    assert service.task_get(task['id'])['executor'] is None


def test_active_background_is_visible_while_agent_releases_lease(service, task):
    with service.store.transaction() as db:
        operation = service.store.queue(db, task['id'], 'decision', {'events': []})['operation_id']
        db.execute("UPDATE queue SET state='running' WHERE id=?", (operation,))
    lease = service.task_claim(task['id'], 'custom-background-owner')
    progress = service.task_get(task['id'])
    assert progress['executor']['owner'] == 'custom-background-owner'
    assert progress['active_background'][0]['operation_id'] == operation
    service.task_release(task['id'], lease['token'])
    progress = service.task_get(task['id'])
    assert progress['executor'] is None
    assert progress['active_background'][0]['operation_id'] == operation


@pytest.mark.parametrize('failure', ['missing_output', 'missing_input'])
def test_analysis_receipt_distinguishes_preparation_and_output_failure(service, task, worker, failure):
    root = service.store.root(task['id'])
    (root / 'data.txt').write_text('preserved data')
    (root / 'analysis.py').write_text("print('finished without expected output')\n")
    operation = service.analysis_submit(task['id'], 1, 'analysis.py', ['data.txt'], ['result.csv'], 'analysis')
    if failure == 'missing_input':
        (root / 'data.txt').unlink()
    worker.work_one()
    result = service.operation_get(operation['operation_id'])
    assert result['state'] == 'failed'
    error = result['result']['error']
    evidence = error['evidence']
    assert evidence['missing_outputs'] == ['result.csv']
    if failure == 'missing_output':
        assert error['code'] == 'output_missing' and error['effect'] == 'partial'
        assert evidence['exit_code'] == 0
        assert 'finished without expected output' in service.file_read(task['id'], evidence['log_path'])['text']
    else:
        assert error['effect'] == 'saved_needs_correction'
        assert evidence['exit_code'] is None and evidence['log_path'] is None
    assert json.loads(service.file_read(task['id'], evidence['receipt_path'])['text'])['status'] == 'failed'
