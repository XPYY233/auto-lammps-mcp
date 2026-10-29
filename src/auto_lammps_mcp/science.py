"""Dimensional checks and data inspection from actual result files."""
from __future__ import annotations

import math
import re
from pint import UnitRegistry
from .core import Fault, require

UNITS = UnitRegistry()


def normalize(unit):
    return unit.replace("Å", "angstrom").replace("Å", "angstrom").replace("³", "**3").replace("²", "**2").replace("^", "**")


def convert(value, unit, target):
    require(math.isfinite(value), "数值应为有限数")
    try:
        quantity = UNITS.Quantity(value, normalize(unit)).to(normalize(target))
    except Exception as exc:
        raise Fault("unit_error", str(exc), "检查量纲与单位表达") from exc
    return dict(original=dict(value=value, unit=unit), value=quantity.magnitude, unit=target, dimensionality=str(quantity.dimensionality))


def thermo_blocks(path):
    """Keep each LAMMPS thermo block separate; resets and differing columns are not silently merged."""
    blocks, current = [], None
    with open(path, errors="replace") as stream:
        for number, line in enumerate(stream, 1):
            fields = line.split()
            if fields and fields[0] in ("Step", "Time") and len(fields) > 1:
                current = dict(columns=fields, rows=[], first_line=number)
                blocks.append(current)
            elif current and len(fields) == len(current["columns"]):
                try:
                    values = [float(x) for x in fields]
                    require(all(math.isfinite(v) for v in values), f"第 {number} 行存在非有限数据")
                    current["rows"].append(values)
                except ValueError:
                    current = None
            elif current:
                current = None
    return blocks


def inspect_dump(path):
    """Inspect per-atom trajectory identities and columns without treating averages as local data."""
    frames, ids = [], None
    with open(path) as stream:
        while True:
            line = stream.readline()
            if not line:
                break
            require(line.strip() == "ITEM: TIMESTEP", "轨迹帧头不完整")
            step = int(stream.readline())
            require(stream.readline().strip() == "ITEM: NUMBER OF ATOMS", "轨迹缺少原子数")
            count = int(stream.readline())
            require(count > 0 and stream.readline().startswith("ITEM: BOX BOUNDS"), "轨迹盒子信息无效")
            box = [stream.readline().strip() for _ in range(3)]
            for bounds in box:
                numbers = [float(x) for x in bounds.split()]
                require(len(numbers) >= 2 and all(math.isfinite(x) for x in numbers) and numbers[1] > numbers[0], '轨迹盒子范围无效')
            header = stream.readline().split()
            require(header[:2] == ["ITEM:", "ATOMS"], "轨迹缺少逐原子数据")
            columns = header[2:]
            require("id" in columns, "轨迹需要原子身份 id")
            frame_ids = set()
            for _ in range(count):
                values = stream.readline().split()
                require(len(values) == len(columns), "轨迹帧不完整")
                require(all(math.isfinite(float(x)) for name, x in zip(columns, values) if name != 'element'), '轨迹包含非有限原子数据')
                atom = int(values[columns.index("id")])
                require(atom not in frame_ids, "同一帧存在重复原子身份")
                frame_ids.add(atom)
            identity_changed = ids is not None and ids != frame_ids
            frames.append(dict(step=step, atoms=count, columns=columns, box=box, identity_changed=identity_changed))
            ids = frame_ids
    require(frames, "没有读到完整轨迹帧")
    return dict(frames=frames, frame_count=len(frames))

