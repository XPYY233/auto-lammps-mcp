from auto_lammps_mcp.verification import run_checks
from auto_lammps_mcp.research_store import tokens


def test_element_alias_is_not_a_substring_of_a_person_name():
    assert 'nickel' not in tokens('Marinica W potential')
    assert 'nickel' in tokens('Ni 势函数')
    assert 'tungsten' in tokens('钨势函数')


def test_resource_search_handles_natural_keywords_and_reports_catalog_size(service, tmp_path):
    path = tmp_path / 'W.eam'; path.write_text('native potential')
    resource = service.resource_register('Marinica EAM3 W potential', 'potential', str(path), {'source': 'fixture', 'license': 'test', 'elements': ['W']}, 'register')
    assert service.resource_search('tungsten W Marinica EAM')['resources'][0]['id'] == resource['id']
    assert service.resource_search('钨势函数')['resources'][0]['id'] == resource['id']
    empty = service.resource_search('not-present-name')
    assert empty['total'] == 0 and empty['catalog_total'] == 1


def test_registered_native_file_is_readable_and_bundle_checks_current_bytes(service, tmp_path):
    path = tmp_path / 'pot'
    path.write_text('pair_style eam/fs\n')
    resource = service.resource_register('W invocation', 'author_project', str(path), {'source': 'fixture', 'license': 'test'}, 'native')
    assert service.resource_file_read(resource['id'], 'pot')['text'] == path.read_text()
    bundle = service.resource_bundle([resource['id']], 'W resource set', {'source': 'fixture', 'license': 'test'}, 'bundle')
    assert service.resource_get(resource['id'])['related_bundles'][0]['id'] == bundle['id']
    assert service.resource_get(bundle['id'])['files_current']
    path.write_text('changed invocation\n')
    result = service.resource_get(bundle['id'])
    assert not result['files_current']
    assert result['member_metadata_current']
    assert result['member_records'][0]['files_current'] is False
    path.unlink()
    assert not service.resource_get(bundle['id'])['files_current']


def test_measured_failure_is_automatically_retrieved_with_evidence(service, task):
    root = service.store.root(task['id'])
    (root / 'metrics.json').write_text('{"drift":20}')
    receipt = run_checks(service.store, task['id'], [dict(kind='json', path='metrics.json', bounds={'drift': {'max': 1}})])
    new = service.task_create('Ni energy drift', 'Find prior energy drift failures', [{'id': 'plot', 'description': 'energy plot'}], 'new')
    memories = service.memory_search(new['id'], 'drift')['items']
    match = next(x for x in memories if x['evidence'].get('receipt_id') == receipt['id'])
    assert match['kind'] == 'failure' and match['status'] == 'observed'
    assert match['evidence']['files']['metrics.json'] == receipt['sources']['metrics.json']
    assert '20' in match['summary']
    assert match['evidence_current'] is True
