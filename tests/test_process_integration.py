import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.transport import call


def start(home):
    process = subprocess.Popen([sys.executable, "-m", "auto_lammps_mcp.cli", "--home", str(home), "serve"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        try:
            if call(home, "doctor")["daemon"].get("heartbeat"):
                return process
        except Fault:
            if process.poll() is not None:
                raise AssertionError(process.communicate()[1].decode())
            time.sleep(0.05)
    process.terminate()
    raise AssertionError("service failed to become ready")


def stop(process):
    process.terminate()
    process.communicate(timeout=10)


@pytest.mark.asyncio
async def test_mcp_actual_tools_and_daemon_restart(tmp_path):
    home = tmp_path / "state"
    process = start(home)
    try:
        first_boot = call(home, "doctor")["daemon"]["heartbeat"]["boot_id"]
        params = StdioServerParameters(command=sys.executable, args=["-m", "auto_lammps_mcp.cli", "--home", str(home), "mcp", "--host", "integration-test"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert "task_create" in {t.name for t in tools.tools}
                annotations = {t.name: t.annotations for t in tools.tools}
                assert annotations["doctor"].readOnlyHint is True
                assert annotations["run_submit"].readOnlyHint is False
                response = await client.call_tool("task_create", dict(title="MCP research", request="Analyze existing output", requirements=[dict(id="plot", description="plot")], op_id="mcp-create"))
                assert not response.isError
                task = json.loads(response.content[0].text)
                assert task["request"] == "Analyze existing output"
                inspected = await client.call_tool("task_get", {"task_id": task["id"]})
                assert not inspected.isError
                import base64
                root = home / "projects" / task["id"]
                (root / "plot.png").write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg=="))
                preview = await client.call_tool("artifact_preview", dict(task_id=task["id"], path="plot.png"))
                assert not preview.isError
                assert any(item.type == "image" for item in preview.content)
        stop(process)
        process = start(home)
        restored = call(home, "task_get", {"task_id": task["id"]})
        assert restored["request"] == task["request"]
        heartbeat = call(home, "doctor")["daemon"]["heartbeat"]
        assert heartbeat["boot_id"] != first_boot
        assert heartbeat["pid"] == process.pid
        assert heartbeat["observed_at"] >= heartbeat["started_at"]
        assert call(home, "doctor")["host_calls"]["integration-test"]["tool"] == "artifact_preview"
    finally:
        stop(process)


def test_concurrent_daemon_start_keeps_one_writer(tmp_path):
    home = tmp_path / "state"
    process = start(home)
    try:
        other = subprocess.run([sys.executable, "-m", "auto_lammps_mcp.cli", "--home", str(home), "serve"], capture_output=True, timeout=10)
        assert other.returncode == 0
        assert process.poll() is None
        assert call(home, "doctor")["version"]
    finally:
        stop(process)


def test_daemon_keeps_monitoring_during_analysis(tmp_path):
    home = tmp_path / "state"
    process = start(home)
    try:
        task = call(home, "task_create", dict(title="Background", request="analyze data", requirements=[dict(id="a", description="result")], op_id="create"))
        root = home / "projects" / task["id"]
        root.mkdir(exist_ok=True)
        (root / "in.txt").write_text("data")
        (root / "analysis.py").write_text("import time\nfrom pathlib import Path\ntime.sleep(2)\nPath('out.txt').write_text('result')\n")
        op = call(home, "analysis_submit", dict(task_id=task["id"], revision=1, script="analysis.py", inputs=["in.txt"], outputs=["out.txt"], op_id="analyze"))
        latencies = []
        for _ in range(30):
            before = time.monotonic()
            state = call(home, "operation_get", {"operation_id": op["operation_id"]})
            latencies.append(time.monotonic() - before)
            if state["state"] == "completed":
                break
            time.sleep(0.2)
        assert state["state"] == "completed"
        assert max(latencies) < 2
        assert Path(state["result"]["outputs"]["out.txt"]["path"]).read_text() == "result"
    finally:
        stop(process)

