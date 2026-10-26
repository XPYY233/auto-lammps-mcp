"""Bounded, read-only output inspection; also runs as a standalone remote script."""
import json
import math
from pathlib import Path
import stat
import sys
import time


def thermo_tail(text, offset):
    lines = text.splitlines(keepends=True)
    if offset and lines:
        lines.pop(0)  # A tail can begin inside a row.
    if lines and not lines[-1].endswith("\n"):
        lines.pop()  # A writer may still be appending the last row.
    columns, latest = None, None
    for line in lines:
        fields = line.split()
        if fields and fields[0] in ("Step", "Time") and len(fields) > 1:
            columns = fields
            latest = None  # A new block may reset steps and change columns.
            continue
        if columns and len(fields) == len(columns):
            try:
                values = [float(value) for value in fields]
                if all(math.isfinite(value) for value in values):
                    latest = dict(columns=columns, values=dict(zip(columns, values)))
                    continue
            except ValueError:
                pass
        columns = None
    return latest


def read_file(root, relative, offset=None, limit=65536):
    root = Path(root).resolve()
    name = Path(relative)
    if name.is_absolute() or ".." in name.parts or not 1 <= limit <= 262144 or (offset is not None and offset < 0):
        raise ValueError("invalid output path or byte range")
    path = (root / name).resolve()
    path.relative_to(root)
    if not path.exists():
        return dict(path=relative, exists=False, observed_at=time.time())
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("output must be a regular file")
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        start = max(0, size - limit) if offset is None else min(offset, size)
        stream.seek(start)
        data = stream.read(min(limit, size - start))
    text = data.decode("utf-8", errors="replace")
    return dict(path=relative, exists=True, bytes=size, modified_at=path.stat().st_mtime, observed_at=time.time(),
                offset=start, next_offset=start + len(data), truncated=start > 0 or start + len(data) < size,
                text=text, thermo=thermo_tail(text, start))


def main(requests):
    for request in requests:
        result = dict(run_id=request["run_id"], observed_at=time.time(), files=[])
        for relative in request["paths"]:
            try:
                result["files"].append(read_file(request["root"], relative, request.get("offset"), request.get("limit", 65536)))
            except (OSError, ValueError) as exc:
                result["files"].append(dict(path=relative, error=str(exc)))
        print("ALM_OUTPUT\t" + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main(json.loads(sys.argv[1]))
