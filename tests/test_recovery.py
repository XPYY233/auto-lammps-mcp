import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.service import Service
from conftest import prepare, revision


def test_duplicate_operation_and_reused_key_conflict(service):
    arguments = dict(title="Ni", request="goal", requirements=[dict(id="r", description="result")], op_id="same")
    first = service.task_create(**arguments)
    assert service.task_create(**arguments) == first
    with pytest.raises(Fault, match="不同输入"):
        service.task_create(**{**arguments, "title": "Fe"})


def test_concurrent_same_submit_different_keys_dispatches_once(service, task, worker, scheduler):
    run = prepare(service, worker, task)
    rev = revision(service, task)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda op: service.run_submit(run["id"], rev, op), ["submit-a", "submit-b"]))
    worker.dispatch_one()
    worker.dispatch_one()
    assert scheduler.submissions == 1


def test_lost_reply_reconciles_without_second_submission(service, task, worker, scheduler):
    run = prepare(service, worker, task)
    service.run_submit(run["id"], revision(service, task), "submit")
    scheduler.lose_receipt = True
    worker.dispatch_one()
    assert service.run_get(run["id"])["state"] == "unknown"
    service.run_submit(run["id"], revision(service, task), "new-host-key")
    worker.dispatch_one()
    worker.poll()
    assert scheduler.submissions == 1
    assert service.run_get(run["id"])["job_id"] == "12345"


def test_stop_while_receipt_unknown_survives_reopen(service, task, worker, scheduler):
    run = prepare(service, worker, task)
    service.run_submit(run["id"], revision(service, task), "submit")
    scheduler.lose_receipt = True
    worker.dispatch_one()
    service.task_control(task["id"], revision(service, task), "stop", "stop")
    reopened = Service(service.store.home)
    assert reopened.task_get(task["id"])["state"] == "stopping"
    worker.poll()
    worker.work_one()
    assert scheduler.cancelled == [run["id"]]
    assert scheduler.submissions == 1


def test_pause_prevents_dispatch_but_retains_current_job(service, task, worker, scheduler):
    run = prepare(service, worker, task)
    service.run_submit(run["id"], revision(service, task), "submit")
    service.task_control(task["id"], revision(service, task), "pause", "pause")
    worker.dispatch_one()
    assert scheduler.submissions == 0
    service.task_control(task["id"], revision(service, task), "resume", "resume")
    worker.dispatch_one()
    service.task_control(task["id"], revision(service, task), "pause", "pause-again")
    worker.poll()
    assert service.run_get(run["id"])["state"] == "running"
    assert scheduler.cancelled == []


def test_stale_lease_cannot_write_after_handoff(service, task):
    lease = service.task_claim(task["id"], "old-agent")
    with pytest.raises(Fault, match="另一执行者"):
        service.task_update(task["id"], 1, {"title": "new"}, "rename", "rename")
    service.task_control(task["id"], 1, "pause", "pause")
    with pytest.raises(Fault, match="操作权已过期"):
        service.task_update(task["id"], 2, {"title": "new"}, "rename", "stale", token=lease["token"])


def test_rules_are_recorded_at_submission(service, task, worker):
    run = prepare(service, worker, task)
    service.task_update(task["id"], revision(service, task), {"policy": {"wall_seconds": 60, "source": "updated user request"}}, "short run", "rules")
    service.run_submit(run["id"], revision(service, task), "submit")
    assert service.run_get(run["id"])["rules"]["wall_seconds"] == 60


def test_scientific_change_invalidates_pending_dispatch(service, task, worker, scheduler):
    run = prepare(service, worker, task)
    service.run_submit(run["id"], revision(service, task), "submit")
    service.task_update(task["id"], revision(service, task), {"constraints": {"temperature_K": 600}}, "user changed conditions", "conditions")
    worker.dispatch_one()
    assert scheduler.submissions == 0
    assert service.run_get(run["id"])["state"] == "cancelled_before_dispatch"


def test_credentials_do_not_own_job_identity(service, task, worker):
    run = prepare(service, worker, task)
    service.run_submit(run["id"], revision(service, task), "submit")
    worker.dispatch_one()
    cluster = service.store.setting("cluster")
    service.setup_configure("cluster", cluster, "credentials-refreshed")
    worker.poll()
    assert service.run_get(run["id"])["job_id"] == "12345"


def test_two_query_failures_preserve_last_state(service, task, worker, scheduler):
    run = prepare(service, worker, task)
    service.run_submit(run["id"], revision(service, task), "submit")
    worker.dispatch_one()
    worker.poll()
    scheduler.fail_query = True
    worker.poll()
    worker.poll()
    record = service.run_get(run["id"])
    assert record["state"] == "running" and record["failures"] == 2
    assert record["evidence"]["confirmed_at"] > 0
    assert any(e["kind"] == "connection_lost" for e in service.events()["events"])


def test_submission_limit_does_not_block_file_diagnosis(service, worker):
    task = service.task_create("Limited research", "configured two-attempt policy", [dict(id="r", description="data")], "b-create")
    service.task_update(task["id"], 1, {"policy": {"submission_limit": 2}}, "user limit", "set-limit")
    for n in range(2):
        run = prepare(service, worker, task, intent=str(n), sample=str(n))
        service.run_submit(run["id"], revision(service, task), "submit-" + str(n))
        with service.store.transaction() as db:
            db.execute("UPDATE runs SET state='failed' WHERE id=?", (run["id"],))
    run = prepare(service, worker, task, intent="third", sample="third")
    with pytest.raises(Fault, match="提交次数"):
        service.run_submit(run["id"], revision(service, task), "submit-third")
    assert service.file_read(task["id"], "run.sh")["text"] == "exit 0\n"
    assert service.file_patch(task["id"], revision(service, task), "diagnosis.md", None, "", "diagnosis", "diagnose")["sha256"]

