import os
from pathlib import Path

from auto_lammps_mcp import files
from conftest import revision


def test_large_copies_preserve_bytes_permissions_and_independent_mutation(tmp_path):
    source = tmp_path / 'source'
    source.write_bytes(os.urandom(2 * 1024 * 1024))
    source.chmod(0o640)
    original = files.file_hash(source)
    first, second = tmp_path / 'first', tmp_path / 'second'
    assert files.copy_independent(source, first) in {'copy_on_write', 'copy'}
    files.copy_independent(source, second)
    assert files.file_hash(first) == files.file_hash(second) == original
    assert source.stat().st_ino != first.stat().st_ino != second.stat().st_ino
    assert first.stat().st_mode & 0o777 == 0o640
    with first.open('r+b') as handle:
        handle.write(b'changed analysis input')
    assert files.file_hash(source) == files.file_hash(second) == original
    with source.open('r+b') as handle:
        handle.write(b'new source')
    assert files.file_hash(second) == original


def test_unsupported_clone_falls_back_to_complete_copy(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'target'
    source.write_bytes(b'1' * (1024 * 1024))
    monkeypatch.setattr(files, 'clone_file', lambda *args: False)
    assert files.copy_independent(source, target) == 'copy'
    assert files.file_hash(source) == files.file_hash(target)


def test_analysis_keeps_large_input_snapshot_independent(service, task, worker):
    root = service.store.root(task['id'])
    data = root / 'trajectory.txt'
    data.write_bytes(b'1234\n' * 250000)
    original = files.file_hash(data)
    (root / 'analysis.py').write_text("from pathlib import Path\nPath('trajectory.txt').write_text('changed in attempt')\nPath('result.txt').write_text('done')\n")
    operation = service.analysis_submit(task['id'], revision(service, task), 'analysis.py', ['trajectory.txt'], ['result.txt'], 'analyze')
    worker.work_one()
    result = service.operation_get(operation['operation_id'])
    assert result['state'] == 'completed'
    assert result['result']['sources']['trajectory.txt'] == original
    assert result['result']['input_copies']['trajectory.txt']['bytes'] == 1250000
    assert files.file_hash(data) == original
    assert (Path(result['result']['directory']) / 'trajectory.txt').read_text() == 'changed in attempt'
