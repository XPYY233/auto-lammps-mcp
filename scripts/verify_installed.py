"""Verify a built installation through MCP, native analysis, preview and handoff."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import auto_lammps_mcp
from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.transport import call


ANALYSIS = """import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
values = np.loadtxt('values.csv', delimiter=',', skiprows=1)
stats = {'count': len(values), 'mean': float(np.mean(values[:, 1]))}
Path('statistics.json').write_text(json.dumps(stats))
fig, ax = plt.subplots()
ax.plot(values[:, 0], values[:, 1], marker='o')
ax.set(xlabel='Index', ylabel='Value')
fig.savefig('plot.png', dpi=100)
plt.close(fig)
"""

FAILED_ANALYSIS = """from pathlib import Path
Path('partial.csv').write_text('index,value\\n')
raise ValueError('incomplete column mapping')
"""


async def verify(home, incoming):
    params = StdioServerParameters(command=sys.executable, args=["-m", "auto_lammps_mcp.cli", "--home", str(home), "mcp", "--host", "installed-package-check"])
    with (home / "logs/installed-mcp.log").open("w") as error_log:
      async with stdio_client(params, errlog=error_log) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            async def invoke(method, **values):
                response = await client.call_tool(method, values)
                assert not response.isError, (method, response.content)
                return json.loads(response.content[0].text)

            async def finished(operation, expected="completed"):
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    result = await invoke("operation_get", operation_id=operation["operation_id"])
                    if result["state"] in ("completed", "failed", "interrupted"):
                        if result["state"] != expected:
                            evidence = (result.get("result") or {}).get("error", {}).get("evidence", {})
                            if evidence.get("log_path"):
                                diagnostic = await invoke("file_read", task_id=result["task_id"], path=evidence["log_path"])
                                print(json.dumps(dict(operation_id=operation["operation_id"], diagnostic=diagnostic), ensure_ascii=False), file=sys.stderr)
                        assert result["state"] == expected, result
                        return result["result"]
                    await asyncio.sleep(0.1)
                raise AssertionError("installed queue operation did not finish")

            connection = await invoke("doctor")
            assert connection["current_connection"]["state"] == "current", connection
            task = await invoke("task_create", title="Installed package check", request="Analyze existing values and export the plot with its sources", requirements=[dict(id="plot", description="plot and statistics"), dict(id="bundle", description="complete handoff", kind="archive")], op_id="create")
            tid = task["id"]
            await finished(await invoke("project_import", task_id=tid, revision=task["revision"], source=str(incoming), op_id="import"))
            task = await invoke("task_get", task_id=tid)
            failed = await finished(await invoke("analysis_submit", task_id=tid, revision=task["revision"], script="analyze.py", inputs=["values.csv"], outputs=["partial.csv", "plot.png"], op_id="failed-analysis"), expected="failed")
            assert failed["error"]["effect"] == "partial"
            evidence = failed["error"]["evidence"]
            diagnostic = await invoke("file_read", task_id=tid, path=evidence["log_path"])
            assert "incomplete column mapping" in diagnostic["text"]
            assert evidence["missing_outputs"] == ["plot.png"]
            task = await invoke("task_get", task_id=tid)
            await invoke("file_patch", task_id=tid, revision=task["revision"], path="analyze.py", expected_sha256=evidence["sources"]["analyze.py"], old=FAILED_ANALYSIS, new=ANALYSIS, op_id="repair-analysis")
            task = await invoke("task_get", task_id=tid)
            source_copy = str(Path(evidence["directory"]).relative_to(Path(task["project_path"])) / "values.csv")
            analysis = await finished(await invoke("analysis_submit", task_id=tid, revision=task["revision"], script="analyze.py", inputs=[], input_bindings={"values.csv": source_copy}, outputs=["statistics.json", "plot.png"], op_id="analysis"))
            assert analysis["input_bindings"] == {"values.csv": source_copy}
            assert analysis["sources"][source_copy] == evidence["sources"]["values.csv"]
            root = Path(task["project_path"])
            plot = Path(analysis["outputs"]["plot.png"]["path"]).relative_to(root).as_posix()
            statistics = Path(analysis["outputs"]["statistics.json"]["path"]).relative_to(root).as_posix()
            actual = json.loads((await invoke("file_read", task_id=tid, path=statistics))["text"])
            assert actual == {"count": 3, "mean": 4.0}, actual
            (root / "content-check.json").write_text(json.dumps(dict(actual=actual, expected_mean=4.0)))
            task = await invoke("task_get", task_id=tid)
            registered = await invoke("artifact_register", task_id=tid, revision=task["revision"], path=plot, sources=["values.csv", "analyze.py", statistics], requirement_ids=["plot"], checks=[dict(passed=True, method="Three input rows and mean 4; PNG decoder response", evidence="content-check.json")], op_id="register")
            preview = await client.call_tool("artifact_preview", dict(task_id=tid, path=plot))
            assert not preview.isError
            assert any(item.type == "image" for item in preview.content)
            before = await invoke("completion_check", task_id=tid, revision=registered["revision"], op_id="before-export")
            assert not before["complete"] and [r["id"] for r in before["missing"]] == ["bundle"]
            snapshots = []
            for index in range(31):
                current = await invoke("task_get", task_id=tid)
                operation = await invoke("project_snapshot", task_id=tid, revision=current["revision"], op_id="history-" + str(index))
                snapshots.append(operation["operation_id"])
                snapshot = await finished(operation)
                assert snapshot["snapshot_id"]
                if index == 0:
                    entries, offset = {}, 0
                    while True:
                        page = await invoke("operation_read", operation_id=operation["operation_id"], path=["result", "files"], offset=offset, limit=50)
                        assert len(json.dumps(page, ensure_ascii=False, indent=2)) < 6000
                        entries.update({item["key"]: item["value"] for item in page["items"]})
                        offset = page["next_offset"]
                        if not page["more"]:
                            break
                    assert len(entries) >= 500
                    assert all("sha256" in value for value in entries.values())
                    complete = await invoke("operation_get", operation_id=operation["operation_id"], include_details=True)
                    assert len(json.dumps(complete, ensure_ascii=False, indent=2)) < 6000
                    assert complete["result"]["snapshot_id"] == snapshot["snapshot_id"]
                    stored, offset = {}, 0
                    while True:
                        page = await invoke("response_read", response_id=complete["response_id"], path=["result", "files"], offset=offset, limit=50)
                        stored.update({item["key"]: item["value"] for item in page["items"]})
                        if not page["more"]:
                            break
                        offset = page["next_offset"]
                    assert stored == entries
            current = await invoke("task_get", task_id=tid)
            assert len(current["work"]) == 5 and current["record_counts"]["work"] > 30
            optimized = await finished(await invoke("project_optimize", task_id=tid, revision=current["revision"], op_id="optimize-storage"))
            assert optimized["files_preserved"]
            current = await invoke("task_get", task_id=tid)
            export_operation = await invoke("project_export", task_id=tid, revision=current["revision"], op_id="export")
            exported = await finished(export_operation)
            export_progress = (await invoke("operation_get", operation_id=export_operation["operation_id"]))["progress"]
            assert export_progress["phase"] == "completed"
            assert export_progress["files_done"] == export_progress["files_total"] == exported["files"]
            assert export_progress["input_bytes_done"] == export_progress["input_bytes_total"]
            with zipfile.ZipFile(exported["path"]) as archive:
                assert "project/" + plot in archive.namelist()
                record = json.loads(archive.read("research-record.json"))
                assert record["request"] == task["request"]
                assert len(record["work"]) == record["record_counts"]["work"]
                assert set(snapshots) <= {item["id"] for item in record["work"]}
                assert all(isinstance(item["payload"], dict) for item in record["work"])
            task = await invoke("task_get", task_id=tid)
            complete = await invoke("completion_check", task_id=tid, revision=task["revision"], op_id="complete")
            assert complete["complete"], complete
            next_task = await invoke("task_create", title="Handoff receiver", request="Continue the original project", requirements=[dict(id="analysis", description="further analysis")], op_id="next")
            imported = await finished(await invoke("project_import", task_id=next_task["id"], revision=next_task["revision"], source=exported["path"], op_id="restore-handoff"))
            assert imported["original_request"] == task["request"]
            reread = await invoke("file_read", task_id=next_task["id"], path=statistics)
            assert json.loads(reread["text"]) == actual
            return dict(version=auto_lammps_mcp.__version__, package_path=auto_lammps_mcp.__file__, checks=["packaged_skill_and_plan", "MCP", "failed_analysis_evidence", "native_analysis", "prior_result_binding", "storage_optimization", "image_preview", "required_archive", "complete_operation_history", "large_snapshot_pages", "bounded_full_mcp_response", "handoff_reimport"], original_files=exported["files"], exported_operations=len(record["work"]), physics_submissions=0)


def main():
    package = Path(auto_lammps_mcp.__file__).parent
    for path in ("data/skill/SKILL.md", "data/docs/APPROVED_PLAN.md", "data/requirements.lock.txt"):
        assert (package / path).is_file(), path
    with tempfile.TemporaryDirectory(prefix="alm-installed-") as temporary:
        home = Path(temporary) / "state"
        incoming = Path(temporary) / "input"
        incoming.mkdir()
        (incoming / "assets").mkdir()
        for index in range(500):
            (incoming / "assets" / f"input-{index:04}.txt").write_text(str(index))
        (incoming / "analyze.py").write_text(FAILED_ANALYSIS)
        (incoming / "values.csv").write_text("index,value\n0,2\n1,4\n2,6\n")
        process = subprocess.Popen([sys.executable, "-m", "auto_lammps_mcp.cli", "--home", str(home), "serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                try:
                    call(home, "doctor")
                    break
                except Fault:
                    if process.poll() is not None:
                        raise AssertionError(process.communicate()[1].decode())
                    time.sleep(0.05)
            else:
                raise AssertionError("installed service did not start")
            print(json.dumps(asyncio.run(verify(home, incoming)), ensure_ascii=False))
        finally:
            process.terminate()
            process.communicate(timeout=10)


if __name__ == "__main__":
    main()
