"""Read a deployment-selected fixed resource envelope for new research tasks."""
import json
import os
from pathlib import Path

from .core import require


def resources():
    name = os.environ.get("ALM_RESOURCE_PROFILE")
    if not name:
        return {"cores": 32, "memory_mib": 8192, "wall_seconds": 604800}
    values = json.loads(Path(name).read_text())
    require(isinstance(values, dict) and set(values) == {"cores", "memory_mib", "wall_seconds"},
            "Resource profile requires cores, memory_mib and wall_seconds")
    for key, upper in (("cores", 32), ("memory_mib", 8192), ("wall_seconds", 604800)):
        require(type(values[key]) is int and 1 <= values[key] <= upper, f"Invalid resource profile {key}")
    return values


def check_resources(policy):
    cap = resources()
    require(all(type(policy[key]) is int for key in cap)
            and policy["cores"] == cap["cores"] and policy["memory_mib"] == cap["memory_mib"]
            and 1 <= policy["wall_seconds"] <= cap["wall_seconds"],
            f"Resource profile requires {cap['cores']} CPU(s), {cap['memory_mib']} MiB and at most {cap['wall_seconds']} seconds")
