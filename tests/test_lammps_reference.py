import json
import pytest

from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.lammps_reference import review_text


@pytest.mark.parametrize('prefix', ['', 'run 0\n', 'run 0\nwrite_data initial.data\n'])
def test_original_compute_not_consumed_failures(prefix):
    result = review_text('compute peratom all pe/atom\n' + prefix + 'write_dump all custom initial.dump id c_peratom\n')
    assert result['errors'] == 1
    assert result['findings'][0]['code'] == 'compute_not_current'
    assert result['findings'][0]['computes'] == ['peratom']


@pytest.mark.parametrize('consumer', [
    'dump d all custom 1 frames.dump id c_peratom\n',
    'compute total all reduce sum c_peratom\nthermo_style custom step temp c_total\n',
])
def test_native_consumers_and_state_changes(consumer):
    text = 'compute peratom all pe/atom\n' + consumer + 'run 0\nwrite_dump all custom initial.dump id c_peratom\n'
    assert review_text(text)['errors'] == 0
    assert review_text(text + 'delete_atoms group vacancy\nwrite_dump all custom changed.dump id c_peratom\n')['errors'] == 1


def test_dynamic_dependencies_are_reported_without_rejecting_native_workflow():
    result = review_text('include initialize.in\nwrite_dump all custom frame.dump id c_peratom\n')
    assert result['errors'] == 0
    assert result['control_flow'] == 'requires_reading_dependencies'


def test_official_reference_cache_paging_and_corruption(service, monkeypatch):
    from auto_lammps_mcp import lammps_reference
    calls = []
    def fetch(url):
        calls.append(url)
        return 'Syntax\ncompute ID group pe/atom\nRestrictions\ncurrent step\n'
    monkeypatch.setattr(lammps_reference, 'fetch_official', fetch)
    first = service.lammps_reference('compute pe/atom', '28Mar2023', limit=8)
    second = service.lammps_reference('compute pe/atom', '28Mar2023', offset=first['next_offset'])
    assert first['text'] + second['text'] == fetch(calls[0])
    assert second['cached'] and not second['more']
    assert len(calls) == 2  # one fetch by the service, one explicit fixture comparison
    assert first['source_url'].endswith('/patch_28Mar2023/doc/src/compute_pe_atom.rst')
    path = next((service.store.home / 'references').rglob('*.json'))
    record = json.loads(path.read_text()); record['text'] = 'altered'; path.write_text(json.dumps(record))
    with pytest.raises(Fault) as exc:
        service.lammps_reference('compute pe/atom', '28Mar2023')
    assert exc.value.code == 'reference_corrupt'
    with pytest.raises(Fault):
        service.lammps_reference('../secret', '28Mar2023')
    with pytest.raises(Fault):
        service.lammps_reference('write_dump', 'latest')


def test_prepare_reviews_frozen_input_not_modified_project(service, task, worker):
    from conftest import prepare, revision
    prepare(service, worker, task)
    root = service.store.root(task['id'])
    (root / 'run.sh').write_text('mpirun -np 32 lmp -in in.ni\n')
    (root / 'in.ni').write_text('compute pa all pe/atom\nrun 0\nwrite_dump all custom bad.dump id c_pa\n')
    op = service.project_snapshot(task['id'], revision(service, task), 'bad-snapshot')
    worker.work_one()
    snapshot = service.operation_get(op['operation_id'])['result']['snapshot_id']
    # An editor changing the project after the snapshot must not hide the bad frozen input.
    (root / 'in.ni').write_text('compute pa all pe/atom\ndump d all custom 1 trajectory id c_pa\nrun 1\n')
    with pytest.raises(Fault) as exc:
        service.run_prepare(task['id'], revision(service, task), snapshot, 'run.sh', 'bad', 'c1', '1', 'bad-prepare')
    assert exc.value.code == 'lammps_input_error'
    assert exc.value.evidence['files'][0]['findings'][0]['line'] == 3
    op = service.project_snapshot(task['id'], revision(service, task), 'good-snapshot')
    worker.work_one()
    good = service.operation_get(op['operation_id'])['result']['snapshot_id']
    run = service.run_prepare(task['id'], revision(service, task), good, 'run.sh', 'good', 'c1', '1', 'good-prepare')
    assert service.run_get(run['id'])['evidence']['input_review']['errors'] == 0
