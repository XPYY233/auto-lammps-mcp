import json
from pathlib import Path

import pytest

from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.files import file_hash
from auto_lammps_mcp.service import Service
from conftest import revision


def test_reuse_prior_output_with_native_relative_paths_after_restart(service, task, worker):
    root = service.store.root(task['id'])
    previous = root / 'analysis/earlier/data'
    previous.mkdir(parents=True)
    data = previous / 'values.txt'
    data.write_text('2\n4\n6\n')
    original_hash = file_hash(data)
    script = root / 'plot.py'
    script.write_text("from pathlib import Path\nv = Path('data/values.txt')\nPath('mean.txt').write_text(str(sum(map(float, v.read_text().split()))/3))\nv.write_text('changed disposable copy')\n")
    script_hash = file_hash(script)
    bindings = {'data/values.txt': 'analysis/earlier/data/values.txt'}
    queued = service.analysis_submit(task['id'], 1, 'plot.py', [], ['mean.txt'], 'reuse', input_bindings=bindings)
    reopened = Service(service.store.home)
    worker.work_one()
    result = reopened.operation_get(queued['operation_id'])
    assert result['state'] == 'completed'
    receipt = result['result']
    assert Path(receipt['outputs']['mean.txt']['path']).read_text() == '4.0'
    assert receipt['sources'][bindings['data/values.txt']] == original_hash
    assert receipt['input_bindings'] == bindings
    assert receipt['input_copies']['data/values.txt']['source'] == bindings['data/values.txt']
    assert file_hash(data) == original_hash and file_hash(script) == script_hash
    persisted = json.loads(reopened.file_read(task['id'], receipt['receipt_path'])['text'])
    assert persisted['input_bindings'] == bindings
    assert service.analysis_submit(task['id'], 1, 'plot.py', [], ['mean.txt'], 'reuse', input_bindings=bindings) == queued


@pytest.mark.parametrize('bindings', [
    {'../outside.txt': 'source.txt'}, {'/tmp/outside.txt': 'source.txt'},
    {'data.txt': '../source.txt'}, {'plot.py': 'source.txt'},
    {'./plot.py': 'source.txt'}, {'data.txt/child': 'source.txt'},
    {'analysis.log': 'source.txt'}, {'out.txt': 'source.txt'},
])
def test_conflicting_or_escaping_bindings_do_not_queue(service, task, bindings):
    with pytest.raises(Fault):
        service.analysis_submit(task['id'], 1, 'plot.py', ['data.txt'], ['out.txt'], 'bad', input_bindings=bindings)
    assert revision(service, task) == 1
    assert not service.task_get(task['id'])['work']


def test_missing_bound_source_is_retained_as_preparation_failure(service, task, worker):
    (service.store.root(task['id']) / 'plot.py').write_text("raise RuntimeError('must not run')")
    bindings = {'data.txt': 'analysis/missing/data.txt'}
    queued = service.analysis_submit(task['id'], 1, 'plot.py', [], ['out.txt'], 'missing', input_bindings=bindings)
    worker.work_one()
    failed = service.operation_get(queued['operation_id'])
    assert failed['state'] == 'failed'
    error = failed['result']['error']
    assert error['effect'] == 'saved_needs_correction'
    assert error['evidence']['input_bindings'] == bindings
    assert error['evidence']['exit_code'] is None
    assert 'analysis/missing/data.txt' in error['message']
