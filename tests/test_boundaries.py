import sqlite3
import pytest
from auto_lammps_mcp.core import Fault, Store
from conftest import prepare, revision


def test_original_requirements_cannot_be_deleted_to_pass(service, task):
    with pytest.raises(Fault, match="原始成果"):
        service.task_update(task["id"], 1, {"requirements": [{"id": "different", "description": "easier output"}]}, "change", "delete-goal")


def test_formal_b_requires_independent_execution_identity(service, worker):
    task = service.task_create("B", "independent task", [{"id": "result", "description": "data"}], "create", mode="formal_b")
    run = prepare(service, worker, task)
    assert task["policy"]["submission_limit"] == 2
    with pytest.raises(Fault) as error:
        service.run_submit(run["id"], revision(service, task), "submit")
    assert error.value.code == "isolation_required"


def test_legacy_database_is_not_modified(service, tmp_path):
    file = tmp_path / "legacy.sqlite"
    with sqlite3.connect(file) as db:
        db.execute("CREATE TABLE old_runs(id text)")
        db.execute("INSERT INTO old_runs VALUES('historical-id')")
    result = service.legacy_inspect(str(file))
    assert result["database_sha256_before"] == result["database_sha256_after"]
    assert result["tables"][0]["rows"] == 1


def test_new_service_rejects_foreign_database(tmp_path):
    with sqlite3.connect(tmp_path / "tasks.sqlite3") as db:
        db.execute("CREATE TABLE old_data(id text)")
    with pytest.raises(Fault, match="其他任务数据库"):
        Store(tmp_path)

