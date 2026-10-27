import hashlib
import json
import pytest
from auto_lammps_mcp.core import Fault


def potential_catalog(tmp_path):
    root = tmp_path / 'old-potentials'
    folder = root / 'fixed-model'
    folder.mkdir(parents=True)
    text = 'Ni model\n'
    (folder / 'Ni.eam').write_text(text)
    row = {'metadata': {'name': 'Ni EAM', 'source': {'url': 'https://example.org/author', 'revision': 'fixed'},
                        'license': 'MIT', 'elements': ['Ni'], 'units': 'metal'},
           'files': {'potential': {'name': 'Ni.eam', 'sha256': hashlib.sha256(text.encode()).hexdigest(), 'size': len(text)}},
           'status': 'collected', 'inspection': {'warnings': ['test condition still needed']}}
    record = folder / 'record.json'
    record.write_text(json.dumps(row))
    return root, record


def test_existing_potential_import_preserves_source_and_reads_verified_bytes(service, tmp_path):
    root, record = potential_catalog(tmp_path)
    original = record.read_bytes()
    assert service.resource_catalog_import(str(root), 'import')['local_verified'] == 1
    resource = service.resource_search('Ni')['resources'][0]
    assert resource['provenance']['original_status'] == 'collected'
    assert service.resource_get(resource['id'])['files'][0]['role'] == 'potential'
    first = service.resource_file_read(resource['id'], 'Ni.eam', limit=3)
    rest = service.resource_file_read(resource['id'], 'Ni.eam', offset=first['next_offset'])
    assert first['more'] and not rest['more']
    assert first['text'] + rest['text'] == 'Ni model\n'
    assert record.read_bytes() == original
    service.resource_catalog_import(str(root), 'repeat')
    assert service.resource_search('Ni')['total'] == 1
    (record.parent / 'Ni.eam').write_text('changed')
    with pytest.raises(Fault) as exc:
        service.resource_file_read(resource['id'], 'Ni.eam')
    assert exc.value.code == 'resource_changed'
    assert len(service.resource_catalog_import(str(root), 'corrupt')['failures']) == 1


def test_author_catalog_separates_indexed_and_existing_code_and_pages(service, tmp_path):
    root = tmp_path / 'existing-code'; root.mkdir()
    (root / 'in.ni').write_text('units metal\npair_style eam/alloy\n')
    catalog = tmp_path / 'authors.json'
    rows = [{'repo': 'author/' + str(i), 'commit': 'fixed', 'license': 'MIT',
             'inputs': ['in.ni'], 'potentials': [], 'elements': ['Ni'], 'pair_style': ['eam/alloy']} for i in range(3)]
    catalog.write_text(json.dumps({'tier_a': rows}))
    original = catalog.read_bytes()
    result = service.resource_catalog_import(str(catalog), 'authors', {'author/1': str(root)})
    assert (result['indexed'], result['local_verified']) == (3, 1)
    page = service.resource_search('Ni', limit=2)
    assert page['total'] == 3 and page['more']
    assert not service.resource_search('Ni', offset=page['next_offset'], limit=2)['more']
    local = service.resource_search('author/1')['resources'][0]
    assert 'pair_style eam/alloy' in service.resource_file_read(local['id'], 'in.ni')['text']
    indexed = service.resource_search('author/0')['resources'][0]
    with pytest.raises(Fault):service.resource_file_read(indexed['id'], 'in.ni')
    with pytest.raises(Fault):service.resource_file_read(local['id'], '../authors.json')
    assert catalog.read_bytes() == original


def test_resource_import_reports_unsafe_and_incomplete_entries(service, tmp_path):
    root, record = potential_catalog(tmp_path)
    outside = tmp_path / 'outside'; outside.write_text('private')
    (record.parent / 'Ni.eam').unlink()
    (record.parent / 'Ni.eam').symlink_to(outside)
    assert service.resource_catalog_import(str(root), 'unsafe')['indexed'] == 0
    assert service.resource_search()['total'] == 0
    catalog = tmp_path / 'incomplete.json'
    catalog.write_text(json.dumps({'tier_a': [{'repo': 'author/missing'}]}))
    result = service.resource_catalog_import(str(catalog), 'incomplete')
    assert result['indexed'] == 0 and len(result['failures']) == 1
