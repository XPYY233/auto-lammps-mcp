import json
import zipfile

import pytest

from auto_lammps_mcp.core import Fault


def test_large_native_snapshot_keeps_identifier_and_lossless_manifest(service, task, worker):
    root = service.store.root(task["id"])
    (root / "inputs").mkdir()
    for n in range(500):
        (root / "inputs" / f"试样-{n:04}.txt").write_text(str(n))
    operation = service.project_snapshot(task["id"], 1, "snapshot")
    worker.work_one()
    oid = operation["operation_id"]
    short = service.operation_get(oid)
    full = service.operation_get(oid, include_details=True)
    assert short["result"]["snapshot_id"] == full["result"]["snapshot_id"]
    assert short["truncated"] and len(json.dumps(short, ensure_ascii=False, indent=2)) < 6000
    read = short["result"]["files"]["read"]
    assert read == dict(method="operation_read", operation_id=oid, path=["result", "files"])
    manifest, offset = {}, 0
    while True:
        page = service.operation_read(oid, read["path"], offset=offset, limit=50)
        assert len(json.dumps(page, ensure_ascii=False, indent=2)) < 6000
        assert page["offset"] == offset
        for item in page["items"]:
            assert item["key"] not in manifest
            manifest[item["key"]] = item["value"]
        offset = page["next_offset"]
        if not page["more"]:
            break
    assert offset == 500 and manifest == full["result"]["files"]
    exported = worker.export(task["id"])
    with zipfile.ZipFile(exported["path"]) as archive:
        record = json.loads(archive.read("research-record.json"))
        assert record["work"][0]["result"]["files"] == manifest


def test_long_text_and_nested_values_can_be_read_without_loss(service, task):
    text = ('\"单位 Å³\"\\n\n' * 3000)
    payload = dict(instruction=text, nested={"a/b.~": [dict(text=text)]})
    with service.store.transaction() as db:
        operation = service.store.queue(db, task["id"], "decision", payload)
    oid = operation["operation_id"]
    path = ["payload", "nested", "a/b.~", "0", "text"]
    pieces, offset = [], 0
    while True:
        page = service.operation_read(oid, path, offset=offset)
        assert len(json.dumps(page, ensure_ascii=False, indent=2)) < 6000
        pieces.append(page["text"])
        assert page["next_offset"] > offset
        offset = page["next_offset"]
        if not page["more"]:
            break
    assert "".join(pieces) == text
    parent = service.operation_read(oid, ["payload", "nested"])
    assert parent["items"][0]["details"]["read"]["path"] == ["payload", "nested", "a/b.~"]
    with pytest.raises(Fault):
        service.operation_read(oid, path, offset=len(text) + 1)
    with pytest.raises(Fault):
        service.operation_read(oid, ["payload", "absent"])


def test_work_history_pages_obey_size_and_preserve_every_identity(service, task):
    expected = []
    with service.store.transaction() as db:
        for n in range(10):
            operation = service.store.queue(db, task["id"], "snapshot", dict(number=n))
            expected.append(operation["operation_id"])
            result = dict(snapshot_id="snapshot-" + str(n), files={str(i): dict(sha256="a" * 64) for i in range(500)})
            db.execute("UPDATE queue SET result=? WHERE id=?", (json.dumps(result), operation["operation_id"]))
    actual, offset = [], 0
    while True:
        page = service.task_records(task["id"], "work", offset, limit=10)
        assert len(json.dumps(page, ensure_ascii=False, indent=2)) < 6000
        actual.extend(row["id"] for row in page["records"])
        offset = page["next_offset"]
        if not page["more"]:
            break
    assert actual == expected
def test_generated_help_is_complete_across_bounded_pages(service):
    import inspect
    import json
    expected = {name: fn for name, fn in inspect.getmembers(service, inspect.ismethod) if getattr(fn, "public", False)}
    seen, offset = {}, 0
    while True:
        page = service.help(offset=offset, limit=50)
        assert len(json.dumps(page, ensure_ascii=False, indent=2)) < 6000
        assert not set(seen) & page["tools"].keys()
        seen.update(page["tools"])
        if not page["more"]:
            break
        assert page["next_offset"] > offset
        offset = page["next"]["arguments"]["offset"]
    assert seen.keys() == expected.keys()
    for name, fn in expected.items():
        detail = service.help(method=name)["tools"][name]
        assert detail == seen[name]
        assert detail["signature"] == str(inspect.signature(fn))
