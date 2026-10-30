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
    frames, ids, identities, units = [], None, None, None
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as stream:
        while True:
            line = stream.readline()
            if not line:
                break
            physical_time = None
            while line.strip() in ('ITEM: TIME', 'ITEM: UNITS'):
                if line.strip() == 'ITEM: TIME':
                    physical_time = float(stream.readline())
                    require(math.isfinite(physical_time), '轨迹时间不是有限数')
                else:
                    units = stream.readline().strip()
                line = stream.readline()
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
            frame_types = {}
            for _ in range(count):
                values = stream.readline().split()
                require(len(values) == len(columns), "轨迹帧不完整")
                require(all(math.isfinite(float(x)) for name, x in zip(columns, values) if name != 'element'), '轨迹包含非有限原子数据')
                atom = int(values[columns.index("id")])
                require(atom not in frame_ids, "同一帧存在重复原子身份")
                frame_ids.add(atom)
                frame_types[atom] = {key: values[columns.index(key)] for key in ('type', 'element') if key in columns}
            identity_changed = ids is not None and ids != frame_ids
            changes = sum(any(key in identities[atom] and value != identities[atom][key] for key, value in mapping.items())
                          for atom, mapping in frame_types.items() if identities is not None and atom in identities)
            frames.append(dict(index=len(frames), step=step, time=physical_time, units=units, atoms=count, columns=columns,
                               box=box, cell=cell, identity_changed=identity_changed, type_changes=changes))
            ids, identities = frame_ids, frame_types
    require(frames, "没有读到完整轨迹帧")
    return dict(frames=frames, frame_count=len(frames))


def correlate_trajectory(trajectory, log, phase=None):
    """Match saved frames to actual thermo samples; preserve repeated-step ambiguity.

    Physical time uses dump_modify time or thermo Time, including variable dt.
    https://docs.lammps.org/dump_modify.html#description
    """
    from .phases import blocks, selected
    report, by_step = inspect_dump(trajectory), {}
    for index, block in enumerate(blocks(log)):
        if not selected(block, index, {'phase': phase} if phase else {}) or 'Step' not in block['columns']:
            continue
        for row_index, row in enumerate(block['rows']):
            values = dict(zip(block['columns'], row))
            by_step.setdefault(values['Step'], []).append(dict(block=index, row=row_index, first_line=block['first_line'],
                time=values.get('Time'), phase=block['phase'], values=values, errors=block['errors']))
    for frame in report['frames']:
        candidates = by_step.get(frame['step'], [])
        if frame['time'] is not None:
            candidates = [c for c in candidates if c['time'] == frame['time'] and
                          (not frame['units'] or not c['phase']['units'] or frame['units'] == c['phase']['units'])]
        status = 'matched' if len(candidates) == 1 and not candidates[0]['errors'] else 'ambiguous' if candidates else 'no_exact_sample'
        if status == 'matched' and candidates[0]['time'] is None:
            status = 'time_missing'
        frame.update(match=status, thermo_samples=candidates,
                     physical_time=frame['time'] if frame['time'] is not None else candidates[0]['time'] if status == 'matched' else None)
    report.update(unresolved=sum(f['match'] != 'matched' for f in report['frames']),
                  next_action='根据实际阶段选择对应采样；保留采样间隔和歧义，结构图引用 frame index/Step/实测时间')
    return report

