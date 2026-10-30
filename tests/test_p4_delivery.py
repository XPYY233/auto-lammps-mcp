import json
from pathlib import Path
import zipfile

import pytest

from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.service import Service
from auto_lammps_mcp.worker import Worker
from auto_lammps_mcp.verification import run_checks
from conftest import revision
from test_validation_reuse import qualify
from test_batch_progress import prepare_batch


def collected_batch(service, worker, scheduler, task):
    sid, plan = qualify(service, worker, scheduler, task)
    bid = prepare_batch(service, task, sid, plan, count=4)
    service.batch_submit(bid, revision(service, task), 'batch-submit')
    members = service.batch_get(bid)['members']
    for index, member in enumerate(members):
        worker.dispatch_one()
        scheduler.state = 'failed' if index == 3 else 'completed'
        worker.poll(); worker.work_one()
        root = service.store.root(task['id']) / 'runs' / member['run_id']
        root.mkdir(parents=True, exist_ok=True)
        if index != 2:
            (root / 'value.txt').write_text(str((index + 1) * 2))
    return bid, members


def test_batch_to_native_analysis_revision_memory_and_handoff(service, task, worker, scheduler):
    bid, members = collected_batch(service, worker, scheduler, task)
    dataset = service.batch_dataset(task['id'], bid, ['value.txt'])
    assert (dataset['planned'], dataset['included'], dataset['excluded']) == (4, 2, 2)
    assert dataset['members'][2]['missing_files'] == ['value.txt']
    assert dataset['members'][3]['reason'] == 'run_failed'
    root = service.store.root(task['id'])
    (root / 'stats.py').write_text("""import json
from pathlib import Path
data=json.loads(Path('batch-samples.json').read_text())
values=[float(Path(next(iter(m['files']))).read_text()) for m in data['members'] if m['included']]
Path('stats.json').write_text(json.dumps({'mean':sum(values)/len(values),'n':len(values),'planned':data['planned']}))
""")
    method = dict(purpose='independent repeat mean', unit='eV', window='one value per sample')
    operation = service.analysis_submit(task['id'], revision(service, task), 'stats.py', [], ['stats.json'], 'stats', batch_id=bid, batch_files=['value.txt'], method=method)
    worker.work_one()
    receipt = service.operation_get(operation['operation_id'], include_details=True)['result']
    assert receipt['status'] == 'completed' and receipt['method'] == method
    stats = 'analysis/' + operation['operation_id'] + '/stats.json'
    assert json.loads((root / stats).read_text()) == dict(mean=3., n=2, planned=4)
    memory = service.memory_search(task['id'], query='independent repeat')['items']
    assert any(m['evidence'].get('operation_id') == operation['operation_id'] and m['evidence_current'] for m in memory)
    measured = run_checks(service.store, task['id'], [dict(kind='json', path=stats, values={'mean':3., 'n':2})])
    original = service.artifact_register(task['id'], revision(service, task), stats, [], ['curve'], [{'receipt_id':measured['id']}], 'register', description='Independent sample mean')
    # Keep the original successful analysis when the user asks for another statistic.
    (root / 'spread.py').write_text("import json\nfrom pathlib import Path\nx=json.loads(Path('old.json').read_text()); x['method']='revised presentation'\nPath('stats.json').write_text(json.dumps(x))\n")
    followup = service.analysis_submit(task['id'], revision(service, task), 'spread.py', [], ['stats.json'], 'followup', input_bindings={'old.json':stats}, method={'purpose':'revised presentation'}, supersedes=operation['operation_id'])
    worker.work_one()
    new_path = 'analysis/' + followup['operation_id'] + '/stats.json'
    checked = run_checks(service.store, task['id'], [dict(kind='json', path=new_path, values={'mean':3., 'n':2})])
    service.artifact_register(task['id'], revision(service, task), new_path, [], ['curve'], [{'receipt_id':checked['id']}], 'register-new', supersedes=[original['artifact_id']], description='Updated sample summary')
    view = Service(service.store.home).results_get(task['id'])
    assert not view['missing']
    assert {x['status'] for x in view['artifacts']} == {'current', 'superseded'}
    assert view['analyses'][0]['supersedes'] == operation['operation_id']
    assert (root / stats).is_file()
    requirements = service.task_get(task['id'])['requirements'] + [dict(id='zip', kind='archive', description='full research package')]
    service.task_update(task['id'], revision(service, task), {'requirements': requirements}, 'export requested', 'export-requirement')
    request = service.project_export(task['id'], revision(service, task), 'export')
    worker.work_one()
    exported = service.operation_get(request['operation_id'])['result']
    assert service.completion_check(task['id'], revision(service, task), 'complete')['complete']
    with zipfile.ZipFile(exported['path']) as package:
        assert 'Updated sample summary' in package.read('README.txt').decode()
        assert new_path in package.read('README.txt').decode()
        history = json.loads(package.read('research-record.json'))
        assert history['result_catalog']['analyses'][0]['method']['purpose'] == 'revised presentation'
    received = service.task_create('Continue', 'continue batch analysis', [dict(id='further', description='further analysis')], 'receiver')
    imported = service.project_import(received['id'], 1, exported['path'], 'import')
    worker.work_one()
    assert service.operation_get(imported['operation_id'])['state'] == 'completed'
    view = service.results_get(received['id'])
    assert view['imported_projects'][0]['original_task'] == task['id']
    assert (service.store.root(received['id']) / new_path).read_bytes() == (root / new_path).read_bytes()


def test_batch_changes_while_queued_leave_readable_failure(service, task, worker, scheduler):
    bid, members = collected_batch(service, worker, scheduler, task)
    root = service.store.root(task['id'])
    (root / 'stats.py').write_text("raise RuntimeError('must not run changed input')")
    op = service.analysis_submit(task['id'], revision(service, task), 'stats.py', [], ['result.json'], 'analysis', batch_id=bid, batch_files=['value.txt'])
    (root / 'runs' / members[0]['run_id'] / 'value.txt').write_text('changed')
    worker.work_one()
    result = service.operation_get(op['operation_id'], include_details=True)
    assert result['state'] == 'failed' and result['result']['error']['code'] == 'analysis_input_changed'
    assert result['result']['error']['evidence']['exit_code'] is None
    assert service.results_get(task['id'])['analyses'][0]['receipt_path']


def test_retry_remains_one_sample_in_batch_dataset(service, task, worker, scheduler):
    bid, members = collected_batch(service, worker, scheduler, task)
    old = service.run_get(members[-1]['run_id'])
    with service.store.connect() as db:
        pid = db.execute('SELECT plan_id FROM run_validations WHERE run_id=?', (old['id'],)).fetchone()[0]
    retry = service.run_prepare(task['id'], revision(service, task), old['snapshot_id'], old['entry'], 'retry', old['condition_id'], old['sample_id'], 'retry-prepare', purpose='retry', parent_id=old['id'], validation_id=pid)
    service.run_submit(retry['id'], revision(service, task), 'retry-submit')
    worker.dispatch_one(); scheduler.state='completed'; worker.poll(); worker.work_one()
    file = service.store.root(task['id']) / 'runs' / retry['id'] / 'value.txt'
    file.parent.mkdir(parents=True, exist_ok=True); file.write_text('8')
    data = service.batch_dataset(task['id'], bid, ['value.txt'])
    assert data['planned'] == 4 and data['included'] == 3
    assert data['members'][-1]['run_id'] == retry['id']
    assert len(data['members'][-1]['attempts']) == 2


def test_failed_result_replaced_locally_preserves_other_results(service, task, worker):
    root = service.store.root(task['id'])
    (root/'bad.json').write_text('{"value":20}')
    bad = run_checks(service.store, task['id'], [dict(kind='json', path='bad.json', bounds={'value':{'max':1}})])
    first = service.artifact_register(task['id'], revision(service, task), 'bad.json', [], ['curve'], [{'receipt_id':bad['id']}], 'bad')
    (root/'good.json').write_text('{"value":0.1}')
    good = run_checks(service.store, task['id'], [dict(kind='json', path='good.json', bounds={'value':{'max':1}})])
    service.artifact_register(task['id'], revision(service, task), 'good.json', [], ['curve'], [{'receipt_id':good['id']}], 'good', supersedes=[first['artifact_id']])
    assert service.completion_check(task['id'], revision(service, task), 'complete')['complete']
    assert (root/'bad.json').exists()
    (root/'good.json').write_text('{"value":4}')
    view = service.results_get(task['id'])
    assert view['missing'] == ['curve']
    assert not service.completion_check(task['id'], revision(service, task), 'recheck')['complete']
    with pytest.raises(Fault):
        service.artifact_register(task['id'], revision(service, task), 'good.json', [], [], [{'receipt_id':good['id']}], 'drop-requirement', supersedes=[first['artifact_id']])


FRAME = 'ITEM: TIMESTEP\n{step}\nITEM: NUMBER OF ATOMS\n2\nITEM: BOX BOUNDS pp ff pp\n2 12\n-1 9\n0 10\nITEM: ATOMS id type x y z\n{rows}'


def test_real_time_phase_and_atom_mapping(service, task):
    root = service.store.root(task['id'])
    rows='1 1 3 0 1\n2 2 4 0 1\n'
    swapped='2 2 4 0 1\n1 2 3 0 1\n'
    (root/'atoms.dump').write_text('ITEM: UNITS\nmetal\nITEM: TIME\n0.05\n' + FRAME.format(step=50, rows=rows) + 'ITEM: TIME\n0.07\n' + FRAME.format(step=100, rows=swapped))
    (root/'log').write_text('units metal\nfix ball all nve\nrun 100\nStep Time TotEng\n50 .05 -2\n100 .07 -2\nLoop time\nunfix ball\nfix cool all nvt temp 300 300 .1\nrun 10\nStep Time TotEng\n100 .07 -1\n110 .08 -1\nLoop time\n')
    all_phases = service.trajectory_correlate(task['id'], 'atoms.dump', 'log')
    assert all_phases['frames'][0]['physical_time'] == .05
    assert all_phases['frames'][1]['match'] == 'ambiguous'
    selected = service.trajectory_correlate(task['id'], 'atoms.dump', 'log', phase={'fix_id':'ball'})
    assert selected['unresolved'] == 0 and selected['frames'][1]['physical_time'] == .07
    assert selected['frames'][1]['type_changes'] == 1 and not selected['frames'][1]['identity_changed']
    assert selected['frames'][0]['cell']['origin'] == [2.,-1.,0.]
    failed = run_checks(service.store, task['id'], [dict(kind='trajectory', path='atoms.dump', constant_types=True)])
    assert not failed['passed']
    intentional = run_checks(service.store, task['id'], [dict(kind='trajectory', path='atoms.dump', constant_types=False)])
    assert intentional['passed']


def test_time_never_inferred_from_current_step_size(service, task):
    root=service.store.root(task['id'])
    (root/'atoms.dump').write_text(FRAME.format(step=50, rows='1 1 3 0 1\n2 2 4 0 1\n'))
    (root/'log').write_text('timestep .001\nStep Temp\n50 300\nLoop time\n')
    match = service.trajectory_correlate(task['id'], 'atoms.dump', 'log')
    assert match['frames'][0]['match'] == 'time_missing' and match['frames'][0]['physical_time'] is None
