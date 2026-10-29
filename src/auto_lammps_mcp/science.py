"""Dimensional checks and data inspection from actual result files."""
from __future__ import annotations

import math
import gzip
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
            elif current and fields:
                try:
                    float(fields[0])
                except ValueError:
                    if not line.lstrip().startswith('WARNING:'):
                        current = None
                    continue
                require(len(fields) == len(current['columns']), f'第 {number} 行热力学采样字段不完整')
                try:
                    values = [float(x) for x in fields]
                except ValueError as exc:
                    raise Fault('invalid_thermo_row', f'第 {number} 行热力学采样包含非数值字段', '检查完整原始日志') from exc
                require(all(math.isfinite(v) for v in values), f"第 {number} 行存在非有限数据")
                current["rows"].append(values)
            elif current:
                current = None
    return blocks


def dump_cell(header, lines):
    """Cell vectors are rows; invert LAMMPS enclosing bounds, never defect extents.

    https://docs.lammps.org/Howto_triclinic.html#output-of-restricted-and-general-triclinic-boxes-in-a-dump-file
    """
    rows = [[float(x) for x in line.split()] for line in lines]
    require(all(math.isfinite(x) for row in rows for x in row), '轨迹盒子范围无效')
    flags = header.split()[3:]
    if flags[:2] == ['abc', 'origin']:
        require(all(len(r) == 4 for r in rows), '一般三斜盒需要向量与原点')
        vectors, origin, boundary = [r[:3] for r in rows], [r[3] for r in rows], flags[2:]
    else:
        tilted = flags[:3] == ['xy', 'xz', 'yz']
        require(all(len(r) == (3 if tilted else 2) for r in rows), '轨迹盒子字段数无效')
        xlo, xhi = rows[0][:2]
        ylo, yhi = rows[1][:2]
        zlo, zhi = rows[2][:2]
        xy, xz, yz = (r[2] for r in rows) if tilted else (0., 0., 0.)
        xlo -= min(0., xy, xz, xy+xz)
        xhi -= max(0., xy, xz, xy+xz)
        ylo -= min(0., yz)
        yhi -= max(0., yz)
        require(xhi > xlo and yhi > ylo and zhi > zlo, '轨迹盒子范围无效')
        vectors = [[xhi-xlo, 0., 0.], [xy, yhi-ylo, 0.], [xz, yz, zhi-zlo]]
        origin, boundary = [xlo, ylo, zlo], flags[3:] if tilted else flags
    a, b, c = vectors
    volume = sum(a[i] * (b[(i+1)%3]*c[(i+2)%3] - b[(i+2)%3]*c[(i+1)%3]) for i in range(3))
    require(volume > 0, '轨迹盒矩阵应为右手、非退化晶胞')
    require(not boundary or (len(boundary) == 3 and all(len(x) == 2 and set(x) <= set('pfsm') for x in boundary)), '边界标记无效')
    return dict(origin=origin, vectors=vectors, volume=volume, boundary=boundary,
                periodic=[x == 'pp' for x in boundary] if boundary else None)


def inspect_dump(path):
    """Inspect per-atom trajectory identities and columns without treating averages as local data."""
    frames, ids = [], None
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as stream:
        while True:
            line = stream.readline()
            if not line:
                break
            require(line.strip() == "ITEM: TIMESTEP", "轨迹帧头不完整")
            step = int(stream.readline())
            require(stream.readline().strip() == "ITEM: NUMBER OF ATOMS", "轨迹缺少原子数")
            count = int(stream.readline())
            box_header = stream.readline()
            require(count > 0 and box_header.startswith("ITEM: BOX BOUNDS"), "轨迹盒子信息无效")
            box = [stream.readline().strip() for _ in range(3)]
            cell = dump_cell(box_header, box)
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
            frames.append(dict(step=step, atoms=count, columns=columns, box=box, cell=cell, identity_changed=identity_changed))
            ids = frame_ids
    require(frames, "没有读到完整轨迹帧")
    return dict(frames=frames, frame_count=len(frames))

