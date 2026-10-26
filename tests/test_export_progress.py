import hashlib
import json
import os
from pathlib import Path
import time
import zipfile

from auto_lammps_mcp.service import Service
from conftest import revision


def test_streamed_export_persists_progress_and_preserves_complete_archive(service, task, worker, monkeypatch):
    root = service.store.root(task['id'])
    content = os.urandom(4 * 1024 * 1024)
    (root / 'trajectory.bin').write_bytes(content)
    operation = service.project_export(task['id'], 1, 'export')
    real_time = time.monotonic
    offset = [0]
    monkeypatch.setattr(time, 'monotonic', lambda: real_time() + offset[0])
    original_write = zipfile._ZipWriteFile.write
    observations = []

    def write(stream, chunk):
        offset[0] += 6
        result = original_write(stream, chunk)
        current = Service(service.store.home).operation_get(operation['operation_id'])
        if current.get('progress', {}).get('input_bytes_done', 0):
            observations.append(current['progress'])
            assert current['state'] == 'running'
            visible = service.task_get(task['id'])['work'][0]['progress']
            assert visible == current['progress']
        return result

    monkeypatch.setattr(zipfile._ZipWriteFile, 'write', write)
    worker.work_one()
    result = service.operation_get(operation['operation_id'])
    assert result['state'] == 'completed' and observations
    progress = result['progress']
    assert progress['phase'] == 'completed'
    assert progress['files_done'] == progress['files_total'] == 1
    assert progress['input_bytes_done'] == progress['input_bytes_total'] == len(content)
    assert progress['archive_bytes'] == Path(result['result']['path']).stat().st_size
    assert any(0 < x['input_bytes_done'] < len(content) for x in observations)
    with zipfile.ZipFile(result['result']['path']) as z:
        assert z.read('project/trajectory.bin') == content
        manifest = json.loads(z.read('project-manifest.json'))
        assert manifest['trajectory.bin']['sha256'] == hashlib.sha256(content).hexdigest()


def test_handoff_during_export_keeps_partial_path_then_can_resume(service, task, worker, monkeypatch):
    root = service.store.root(task['id'])
    source = root / 'trajectory.bin'
    source.write_bytes(os.urandom(4 * 1024 * 1024))
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    operation = service.project_export(task['id'], 1, 'export')
    original_write = zipfile._ZipWriteFile.write
    interrupted = []

    def write(stream, chunk):
        result = original_write(stream, chunk)
        if len(chunk) == 1024 * 1024 and not interrupted:
            interrupted.append(True)
            service.task_control(task['id'], revision(service, task), 'pause', 'pause')
        return result

    monkeypatch.setattr(zipfile._ZipWriteFile, 'write', write)
    worker.work_one()
    result = Service(service.store.home).operation_get(operation['operation_id'])
    assert result['state'] == 'interrupted'
    assert result['progress']['phase'] == 'interrupted'
    assert result['progress']['input_bytes_done'] < result['progress']['input_bytes_total']
    previous = result['result']['previous_result']['error']
    assert previous['effect'] == 'partial' and Path(previous['evidence']['path']).is_file()
    assert not service.task_get(task['id'])['deliveries']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    monkeypatch.setattr(zipfile._ZipWriteFile, 'write', original_write)
    service.task_control(task['id'], revision(service, task), 'resume', 'resume')
    retry = service.project_export(task['id'], revision(service, task), 'retry-export')
    worker.work_one()
    success = service.operation_get(retry['operation_id'])
    assert success['state'] == 'completed'
    assert success['result']['path'] != previous['evidence']['path']
    assert Path(previous['evidence']['path']).is_file()
