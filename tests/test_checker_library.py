import json

import pytest

from auto_lammps_mcp.core import Fault
from conftest import revision


def test_native_checker_requires_positive_negative_and_same_program(service, task, worker):
    root = service.store.root(task['id'])
    (root / 'measure.py').write_text("import json\nfrom pathlib import Path\nPath('result.json').write_text(json.dumps({'drift':float(Path('sample.txt').read_text())}))\n")
    cases = []
    for index, value in enumerate((.1, 20)):
        (root / 'sample.txt').write_text(str(value))
        op = service.analysis_submit(task['id'], revision(service, task), 'measure.py', ['sample.txt'], ['result.json'], 'measure-' + str(index))
        worker.work_one()
        cases.append(dict(operation_id=op['operation_id'], output='result.json', expected_passed=value < 1))
    checked = service.checker_register(task['id'], revision(service, task), 'energy drift checker', cases,
                                      dict(kind='json', bounds={'drift': {'max': 1}}), 'register')
    op = service.checker_check(task['id'], revision(service, task), checked['checker_id'], cases[1]['operation_id'], 'result.json', 'check')
    worker.work_one()
    receipt = service.operation_get(op['operation_id'])['result']
    assert not receipt['passed'] and receipt['checks'][0]['measured']['drift'] == 20
    with pytest.raises(Fault, match='预定正反'):
        service.checker_register(task['id'], revision(service, task), 'wrong', cases, dict(kind='json', bounds={'drift': {'max': 100}}), 'wrong')
    assert any(x['evidence'].get('checker_id') == checked['checker_id'] for x in service.memory_search(task['id'], 'energy')['items'])


def test_resource_bundle_retains_members_and_detects_changed_metadata(service):
    a = service.resource_register('W potential', 'potential', '/project/W.eam', {'source': 'fixture', 'license': 'test'}, 'a')
    b = service.resource_register('short-range parameters', 'author_project', '/project/short-range.in', {'source': 'fixture', 'license': 'test'}, 'b')
    value = service.resource_bundle([a['id'], b['id']], 'W combination', {'source': 'fixture', 'license': 'test', 'element_mapping': ['W'], 'short_range': 'specified in member b'}, 'bundle')
    result = service.resource_get(value['id'])
    assert result['member_metadata_current'] and len(result['member_records']) == 2
    with service.store.transaction() as db:
        original = service.store.setting(a['id'])
        service.store.set_setting(db, a['id'], {**original, 'location': '/project/new.eam'})
    assert not service.resource_get(value['id'])['member_metadata_current']
