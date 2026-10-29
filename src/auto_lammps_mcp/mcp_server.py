"""MCP descriptions and schemas come from the same public service operations."""
from __future__ import annotations

import functools
import inspect
import base64
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations, CallToolResult, TextContent, ImageContent
from .core import Fault, encode
from .service import Service
from .transport import call
from .research_store import DISCUSSION_GUIDE


def run(home, host="unknown"):
    from .identity import runtime_identity
    client_identity = {**runtime_identity(), "session_id": uuid.uuid4().hex}
    app = FastMCP("auto-lammps", instructions=DISCUSSION_GUIDE + "使用 help 和 doctor。保存用户原话与成果清单；task_get/research_context 自动检索本地记忆。先复用成套资源与有效验证，只为缺项安排记账 HPC 预验证。直接编辑原生文件，建立快照；validation_match 覆盖后正式或 batch_submit 批量执行。check_run 产生实测回执，再登记成果。变更绑定任务版本及唯一 op_id，重试复用原编号。")
    for name, fn in inspect.getmembers(Service, predicate=inspect.isfunction):
        if not getattr(fn, "public", False):
            continue
        def build(method, function):
            signature = inspect.signature(function)
            signature = signature.replace(parameters=[p for p in signature.parameters.values() if p.name != "self"])
            @functools.wraps(function)
            def tool(**kwargs):
                try:
                    result = call(home, method, kwargs, host=host, client=client_identity)
                    if method == "artifact_preview":
                        content = [TextContent(type="text", text=encode(result))]
                        if result["mime_type"] in ("image/png", "image/jpeg", "image/webp") and result["bytes"] <= 4 * 1024 * 1024:
                            from .core import digest, require
                            data = Path(result["absolute_path"]).read_bytes()
                            require(digest(data) == result["sha256"], "预览读取期间文件发生变化")
                            content.append(ImageContent(type="image", data=base64.b64encode(data).decode(), mimeType=result["mime_type"]))
                        return CallToolResult(content=content)
                    return result
                except Fault as exc:
                    raise ToolError(encode(exc.as_dict())) from exc
            tool.__signature__ = signature
            return tool
        read_only = name in {"help", "doctor", "task_list", "task_get", "task_records", "events", "file_read", "artifact_preview", "operation_get", "operation_read", "response_read", "run_get", "run_log", "units_convert", "data_inspect", "resource_search", "resource_get", "resource_file_read", "legacy_inspect", "lammps_reference", "lammps_input_review"}
        read_only = read_only or name in {'memory_search', 'memory_get', 'research_context', 'research_metrics', 'check_get', 'validation_match', 'batch_get'}
        app.add_tool(build(name, fn), name=name, description=inspect.getdoc(fn),
                     annotations=ToolAnnotations(readOnlyHint=read_only, destructiveHint=not read_only,
                                                 idempotentHint=read_only or "op_id" in inspect.signature(fn).parameters,
                                                 openWorldHint=name in {"connection_check", "run_submit", "run_action", "run_log", "background_request", "lammps_reference"}),
                     structured_output=False if name == "artifact_preview" else None)
    app.run(transport="stdio")

