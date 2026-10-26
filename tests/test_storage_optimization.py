import os
import shutil

import pytest

from auto_lammps_mcp import files
from auto_lammps_mcp.core import Fault
from conftest import revision


def test_optimization_keeps_original_paths_bytes_modes_times_and_snapshots(service, task, worker, monkeypatch):
    root = service.store.root(task['id'])
    source = root / 'source.bin'
    source.write_bytes(os.urandom(2 * 1024 * 1024))
    if hasattr(os, 'setxattr'):
        os.setxattr(source, 'user.alm-check', b'preserve research metadata')
    source.chmod(0o440)
    (root / 'history').mkdir()
    shutil.copy2(source, root / 'history/input.bin')
    snapshot = files.manifest(service.store, task['id'])
    original_time = source.stat().st_mtime_ns
    blob = service.store.home / 'blobs' / snapshot['source.bin']['sha256']
    # Exercise the sharing workflow on filesystems whose test environment lacks cloning.
    if not files.clone_file(blob, root / 'probe'):
        monkeypatch.setattr(files, 'clone_file', lambda a, b: bool(shutil.copy2(a, b)))
    (root / 'probe').unlink(missing_ok=True)
    lease = service.task_claim(task['id'], 'editor')
    queued = service.project_optimize(task['id'], revision(service, task), 'optimize', lease['token'])
    worker.work_one()
    assert service.operation_get(queued['operation_id'])['state'] == 'pending'
    service.task_release(task['id'], lease['token'])
    worker.work_one()
    result = service.operation_get(queued['operation_id'])
    assert result['state'] == 'completed'
    assert len(result['result']['shared']) == 2
    assert files.manifest(service.store, task['id']) == snapshot
    assert source.stat().st_mtime_ns == original_time
    assert source.stat().st_mode & 0o777 == 0o440
    if hasattr(os, 'getxattr'):
        assert os.getxattr(source, 'user.alm-check') == b'preserve research metadata'
    history = root / 'history/input.bin'
    history.chmod(0o640)
    with history.open('r+b') as f:
        f.write(b'changed independent attempt')
    assert files.file_hash(source) == files.file_hash(blob) == snapshot['source.bin']['sha256']


def test_unsupported_volume_leaves_files_untouched(service, task, monkeypatch):
    root = service.store.root(task['id'])
    (root / 'source').write_bytes(b'x' * (1024 * 1024))
    before = files.manifest(service.store, task['id'])
    inode = (root / 'source').stat().st_ino
    monkeypatch.setattr(files, 'clone_file', lambda *args: False)
    result = files.optimize_storage(service.store, task['id'])
    assert not result['shared'] and result['skipped'][0]['reason'] == 'filesystem_clone_unavailable'
    assert (root / 'source').stat().st_ino == inode
    assert files.manifest(service.store, task['id']) == before


def test_bad_snapshot_never_replaces_original(service, task, monkeypatch):
    root = service.store.root(task['id'])
    source = root / 'source'
    source.write_bytes(b'original' * (200000))
    before = files.manifest(service.store, task['id'])
    blob = service.store.home / 'blobs' / before['source']['sha256']
    blob.write_bytes(b'corrupt')
    monkeypatch.setattr(files, 'clone_file', lambda a, b: bool(shutil.copy2(a, b)))
    with pytest.raises(Fault, match='快照摘要'):
        files.optimize_storage(service.store, task['id'])
    assert files.file_hash(source) == before['source']['sha256']
    assert not list((service.store.home / 'blobs').glob('.alm-share-*'))


def test_partial_optimization_reports_preserved_paths_and_stops_on_lease_loss(service, task, monkeypatch):
    root = service.store.root(task['id'])
    for name in ['a', 'b']:
        (root / name).write_bytes(name.encode() * (1024 * 1024))
    before = files.manifest(service.store, task['id'])
    lease = service.task_claim(task['id'], 'optimizer')
    copies = []

    def clone(a, b):
        shutil.copy2(a, b)
        copies.append(str(a))
        if len(copies) == 2:
            service.task_control(task['id'], revision(service, task), 'pause', 'pause-optimization')
        return True

    monkeypatch.setattr(files, 'clone_file', clone)
    b_inode = (root / 'b').stat().st_ino
    with pytest.raises(Fault) as failure:
        files.optimize_storage(service.store, task['id'], lease['token'])
    assert failure.value.code == 'lease_expired' and failure.value.effect == 'partial'
    assert [item['path'] for item in failure.value.evidence['shared']] == ['a']
    assert (root / 'b').stat().st_ino == b_inode
    assert files.manifest(service.store, task['id']) == before
    assert not list((service.store.home / 'blobs').glob('.alm-share-*'))
