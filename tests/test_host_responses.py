import json
import zipfile
import pytest

from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.backup import create_backup
from auto_lammps_mcp.responses import bound_response, bound_error


def reconstruct(service, response_id, path=None):
    path = path or []
    offset, text, items = 0, "", []
    while True:
        page = service.response_read(response_id, path, offset, limit=50)
        assert len(json.dumps(page, ensure_ascii=False, indent=2)) < 6000
        if page["type"] == "value":
            return page["value"]
        text += page.get("text", "")
        for item in page.get("items", []):
            value = item["value"] if "value" in item else reconstruct(service, response_id, item["path"])
            items.append((item["key"], value))
        if not page["more"]:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    return text if page["type"] == "text" else dict(items) if page["type"] == "object" else [value for key, value in items]


def test_full_response_reconstructs_nested_values_and_survives_backup(service, tmp_path):
    value = dict(id="work-original", state="completed", request='多行\\"\n' * 3000,
                 result=dict(snapshot_id="snapshot-retained", files={"input/" + str(i): dict(sha256=str(i), bytes=i) for i in range(500)}),
                 observations=[dict(index=i, value=i / 10, valid=True, note=None) for i in range(90)])
    summary = bound_response(service.store, "operation_get", value)
    assert len(json.dumps(summary, ensure_ascii=False, indent=2)) < 6000
    assert summary["id"] == value["id"] and summary["state"] == "completed"
    assert summary["result"]["snapshot_id"] == "snapshot-retained"
    response_id = summary["response_id"]
    assert reconstruct(service, response_id) == value
    assert bound_response(service.store, "operation_get", value)["response_id"] == response_id
    assert len(list((service.store.home / "responses").iterdir())) == 1
    (service.store.home / "responses/.alm-unfinished").write_text("partial write")
    archive = create_backup(service.store, tmp_path / "backup.zip")
    with zipfile.ZipFile(archive["path"]) as package:
        assert json.loads(package.read("state/responses/" + response_id + ".json")) == value
        assert "state/responses/.alm-unfinished" not in package.namelist()


def test_complete_error_is_available_beyond_host_window(service):
    error = dict(code="remote_command_failed", message="engine diagnostic\n" * 1000, effect="unknown", next_action="read the original job identity")
    bounded = bound_error(service.store, "run_log", error)
    assert len(json.dumps(bounded, indent=2)) < 6000
    assert bounded["code"] == error["code"] and bounded["effect"] == "unknown"
    read = json.loads(bounded["next_action"].split("：", 1)[1])
    assert reconstruct(service, read["response_id"]) == error


def test_large_requested_text_pages_remain_complete_and_readable(service):
    original = 'Measured data with units and identity.\n' * 800
    summary = bound_response(service.store, 'task_get', dict(report=original))
    response_id = summary['response_id']
    offset, text = 0, ''
    while True:
        page = service.response_read(response_id, ['report'], offset, limit=1000, text_limit=5000)
        assert page['applied_limits'] == dict(limit=50, text_limit=4096)
        assert len(json.dumps(page, indent=2)) < 6000
        text += page['text']
        if not page['more']:
            break
        assert page['next_offset'] > offset
        offset = page['next_offset']
    assert text == original


def test_invalid_page_tells_host_which_parameter_to_correct(service):
    summary = bound_response(service.store, 'task_get', dict(report='large data\n' * 1000))
    with pytest.raises(Fault) as error:
        service.response_read(summary['response_id'], ['report'], text_limit=0)
    assert 'text_limit' in str(error.value) and 'text_limit' in error.value.next_action
    corrected = service.response_read(summary['response_id'], ['report'], text_limit=100)
    assert corrected['text'] and corrected['next_offset'] == 100
