"""MCP descriptions and schemas come from the same public service operations."""
from __future__ import annotations

import functools
import inspect

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from .core import Fault, encode
from .service import Service
from .transport import call


def run(home, host="unknown"):
    app = FastMCP("auto-lammps", instructions="使用 help 和 doctor。先保存原始需求及成果清单；直接编辑原生文件，再建立快照。变更使用任务版本和唯一 op_id，重试复用同一编号。物理求值提交到已记账 HPC。任务结果、事件和操作编号可跨对话接续。")
    for name, fn in inspect.getmembers(Service, predicate=inspect.isfunction):
        if not getattr(fn, "public", False):
            continue
        def build(method, function):
            signature = inspect.signature(function)
            signature = signature.replace(parameters=[p for p in signature.parameters.values() if p.name != "self"])
            @functools.wraps(function)
            def tool(**kwargs):
                try:
                    return call(home, method, kwargs, host=host)
                except Fault as exc:
                    raise ToolError(encode(exc.as_dict())) from exc
            tool.__signature__ = signature
            return tool
        app.add_tool(build(name, fn), name=name, description=inspect.getdoc(fn))
    app.run(transport="stdio")

