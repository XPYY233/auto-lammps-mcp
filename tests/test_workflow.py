import json
from conftest import prepare, revision


def test_registered_run_then_analysis_without_conversation(service, task, worker, scheduler):
    run = prepare(service, worker, task)
    root = service.store.root(task["id"])
    (root / "data.txt").write_text("data")
    (root / "analysis.py").write_text("from pathlib import Path\nPath('plot.txt').write_text(Path('data.txt').read_text())\n")
    service.workflow_register(task["id"], revision(service, task), [
        dict(id="simulation", kind="run", after=[], spec=dict(run_id=run["id"])),
        dict(id="plot", kind="analysis", after=["simulation"], spec=dict(script="analysis.py", inputs=["data.txt"], outputs=["plot.txt"]))
    ], "workflow")
    service.task_control(task["id"], revision(service, task), "resume", "start-chain")
    worker.advance_stages()
    worker.dispatch_one()
    scheduler.state = "completed"
    worker.poll()
    worker.work_one()
    worker.advance_stages()
    worker.advance_stages()
    worker.work_one()
    worker.advance_stages()
    stages = service.task_get(task["id"])["stages"]
    assert {s["id"]: s["state"] for s in stages} == {"simulation": "completed", "plot": "completed"}
    assert scheduler.submissions == 1


def test_pause_blocks_next_registered_stage(service, task, worker):
    root = service.store.root(task["id"])
    (root / "data.txt").write_text("data")
    (root / "analysis.py").write_text("print('analysis')")
    service.workflow_register(task["id"], 1, [dict(id="analysis", kind="analysis", after=[], spec=dict(script="analysis.py", inputs=["data.txt"], outputs=["result.txt"]))], "workflow")
    service.task_control(task["id"], revision(service, task), "pause", "pause")
    worker.advance_stages()
    assert service.task_get(task["id"])["stages"][0]["state"] == "pending"

