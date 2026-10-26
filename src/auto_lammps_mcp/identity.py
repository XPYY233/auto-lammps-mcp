"""Identify the code and guide actually loaded by a service or MCP process."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys

from . import __version__


@lru_cache(maxsize=1)
def runtime_identity():
    root = Path(__file__).parent
    code = hashlib.sha256()
    for path in sorted(root.glob("*.py")):
        code.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    guide = root / "data/skill/SKILL.md"
    if not guide.is_file():
        guide = root.parents[1] / "skills/auto-lammps-mcp/SKILL.md"
    source_file = Path(sys.executable).parent.parent / "installation-source.json"
    source = json.loads(source_file.read_text()) if source_file.is_file() else {}
    identity = dict(version=__version__, code_sha256=code.hexdigest(),
                    skill_sha256=hashlib.sha256(guide.read_bytes()).hexdigest() if guide.is_file() else None,
                    installation_identity=source.get("identity"))
    identity["fingerprint"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return identity


def connection_status(call_record, current=None):
    current = current or runtime_identity()
    client = call_record.get("client") or {}
    fingerprint = client.get("fingerprint")
    if not call_record:
        state, action = "not_called", "重新加载平台连接，并在当前对话调用 doctor"
    elif not fingerprint:
        state, action = "unverified_client", "重新加载平台连接，并在当前对话调用 doctor 记录所用版本"
    elif fingerprint != current["fingerprint"]:
        state, action = "reload_required", "当前对话仍连接旧版工具；重新加载 MCP 连接或开启新对话，再调用 doctor"
    else:
        state, action = "current", "当前对话已加载与后台一致的工具"
    return dict(state=state, last_call=call_record.get("last_call"), tool=call_record.get("tool"),
                session_id=client.get("session_id"), client_version=client.get("version"),
                client_fingerprint=fingerprint, next_action=action)
