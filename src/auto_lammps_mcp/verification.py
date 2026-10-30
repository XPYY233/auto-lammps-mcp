"""Execute fixed checks on actual files; callers never supply the verdict."""
from __future__ import annotations

import csv
import json
import math
import time

from .core import Fault, encode, identifier, require
from .files import file_hash
from .research_store import evidence_current, save_memory
from .science import inspect_dump
from .phases import blocks as thermo_blocks, selected, applicability

CHECK_VERSION = '2'
OPTIONS = {
 'file': {'min_bytes', 'contains'},
 'csv': {'columns', 'min_rows', 'bounds'},
 'thermo': {'columns', 'min_rows', 'block', 'phase', 'energy_column', 'max_abs_drift', 'time_column', 'balance'},
 'trajectory': {'columns', 'min_frames', 'atoms'},
 'energy_jump': {'before_block', 'after_block', 'column', 'expected', 'tolerance'},
 'json': {'values', 'bounds'},
}


def validate_checks(checks):
    require(isinstance(checks, list) and 1 <= len(checks) <= 100, '需要 1 至 100 项实际文件检查')
    for check in checks:
        require(isinstance(check, dict) and check.get('kind') in OPTIONS and isinstance(check.get('path'), str),
                '检查需要 kind 和项目相对 path；支持 file、csv、thermo、trajectory、energy_jump')
        require(set(check) <= {'kind', 'path', 'unit', 'basis', 'role', 'action'} | OPTIONS[check['kind']], '检查包含未知参数')
        require(check.get('role', 'required') in ('required', 'advisory', 'candidate'), 'role 使用 required/advisory/candidate')
        require(check.get('action', 'warn') in ('warn', 'stop'), 'action 使用 warn/stop')
        if 'phase' in check:
            require(isinstance(check['phase'], dict) and check['phase'] and set(check['phase']) <=
                    {'fix_id', 'style', 'group', 'marker', 'run_index', 'timestep'}, 'phase 使用执行日志中的 fix_id/style/group/marker/run_index/timestep')
            require('block' not in check, 'phase 与 block 选择一种；推荐实际阶段选择')
        if check.get('balance') == 'task_specific':
            require(isinstance(check.get('basis'), str) and check['basis'].strip(), '任务专属能量变化检查需要 basis 说明依据')
        for key in ('min_rows', 'min_frames', 'min_bytes', 'atoms'):
            if key in check:
                require(type(check[key]) is int and check[key] > 0, key + ' 应为正整数')
        for key in ('max_abs_drift', 'tolerance'):
            if key in check:
                require(type(check[key]) in (int, float) and math.isfinite(check[key]) and check[key] >= 0,
                        key + ' 应为非负有限数')
        if 'bounds' in check:
            require(isinstance(check['bounds'], dict) and check['bounds'], 'bounds 需要明确字段和上下界')
            for field, bounds in check['bounds'].items():
                require(isinstance(field, str) and field and isinstance(bounds, dict) and bounds
                        and set(bounds) <= {'min', 'max'}, '上下界仅支持 min/max，至少填写一项')
                require(all(type(x) in (int, float) and math.isfinite(x) for x in bounds.values()),
                        '上下界需要有限数值')
                require(bounds.get('min', -math.inf) <= bounds.get('max', math.inf), '下界不能超过上界')
        if check['kind'] == 'energy_jump':
            require(all(k in check for k in ('before_block', 'after_block', 'expected', 'tolerance')),
                    '注入能量检查需要明确前后段、期望值和容差')


def execute_check(path, spec):
    kind = spec['kind']
    require(path.is_file() and path.stat().st_size > 0, '检查文件缺失或为空')
    if kind == 'file':
        measured = dict(bytes=path.stat().st_size)
        texts = spec.get('contains', [])
        require(isinstance(texts, list) and all(isinstance(x, str) for x in texts), 'contains 应为文本列表')
        content = path.read_text() if texts else ''
        measured['missing_text'] = [x for x in texts if x not in content]
        return measured, measured['bytes'] >= spec.get('min_bytes', 1) and not measured['missing_text']
    if kind == 'json':
        document = json.loads(path.read_text())
        require(spec.get('values') or spec.get('bounds'), 'JSON 检查需要 values 或 bounds')
        def field(name):
            value = document
            for part in name.split('.'):
                value = value[int(part)] if isinstance(value, list) else value[part]
            return value
        actual, passed = {}, True
        for key, expected in spec.get('values', {}).items():
            actual[key] = field(key)
            passed = passed and actual[key] == expected
        for key, bounds in spec.get('bounds', {}).items():
            value = field(key)
            require(type(value) in (int, float) and math.isfinite(value), '实测量需要有限数')
            actual[key] = value
            passed = passed and bounds.get('min', -math.inf) <= value <= bounds.get('max', math.inf)
        return actual, passed
    if kind == 'trajectory':
        report = inspect_dump(path)
        frames = report['frames']
        measured = dict(frame_count=report['frame_count'], checked_frames=len(frames),
                        identity_changes=sum(f['identity_changed'] for f in frames),
                        atom_counts=sorted({f['atoms'] for f in frames}),
                        missing_columns=sorted(set(spec.get('columns', [])) - set.intersection(*(set(f['columns']) for f in frames))))
        passed = len(frames) >= spec.get('min_frames', 1) and not measured['identity_changes'] and not measured['missing_columns']
        if 'atoms' in spec:
            passed = passed and measured['atom_counts'] == [spec['atoms']]
        return measured, passed
    if kind == 'csv':
        with path.open(newline='') as stream:
            reader = csv.DictReader(stream)
            columns, rows = reader.fieldnames or [], list(reader)
        missing = set(spec.get('columns', [])) - set(columns)
        measured = dict(rows=len(rows), columns=columns, missing_columns=sorted(missing), bounds={})
        passed = not missing and len(rows) >= spec.get('min_rows', 1)
        for column, bounds in spec.get('bounds', {}).items():
            require(column in columns and isinstance(bounds, dict) and set(bounds) <= {'min', 'max'}, '列或上下界无效')
            values = [float(row[column]) for row in rows]
            require(values and all(math.isfinite(x) for x in values), '数据列包含非有限数或没有采样')
            measured['bounds'][column] = dict(min=min(values), max=max(values))
            passed = passed and min(values) >= bounds.get('min', -math.inf) and max(values) <= bounds.get('max', math.inf)
        return measured, passed
    blocks = thermo_blocks(path)
    require(blocks, '没有热力学输出')
    if kind == 'energy_jump':
        before, after = blocks[spec['before_block']], blocks[spec['after_block']]
        require(all(b['rows'] and not b['errors'] for b in (before, after)), '注入前后采样不完整')
        column = spec.get('column', 'TotEng')
        value = after['rows'][0][after['columns'].index(column)] - before['rows'][-1][before['columns'].index(column)]
        measured = dict(delta=value, expected=spec['expected'], absolute_error=abs(value - spec['expected']))
        # Preserve repeated Step values across blocks; injection has two states at one time.
        return measured, measured['absolute_error'] <= spec['tolerance']
    chosen = [(i, b) for i, b in enumerate(blocks) if selected(b, i, spec)]
    require(chosen, '未找到对应执行阶段；读取真实日志中的 fix/unfix/run，或增加运行时阶段标记')
    measured, passed = [], True
    reference = None
    previous_index, previous_generation = -2, None
    for index, block in chosen:
        columns, rows = block['columns'], block['rows']
        missing = set(spec.get('columns', [])) - set(columns)
        item = dict(rows=len(rows), columns=columns, first_line=block['first_line'], missing_columns=sorted(missing), phase=block['phase'])
        if not rows or block['errors']:
            item['errors'] = block['errors'] or ['该阶段没有采样']
            measured.append(item)
            passed = False
            continue
        mismatch = applicability(block['phase'], spec)
        if mismatch:
            item['applicability'] = mismatch
            measured.append(item)
            passed = False
            continue
        passed = passed and not missing and len(rows) >= spec.get('min_rows', 1)
        if 'max_abs_drift' in spec:
            column = spec.get('energy_column', 'TotEng')
            values = [r[columns.index(column)] for r in rows]
            if reference is None or not spec.get('phase') or index != previous_index + 1 or block['phase']['generation'] != previous_generation:
                reference = values[0]
            item['reference'] = reference
            item['max_abs_drift'] = max(abs(x - reference) for x in values)
            passed = passed and item['max_abs_drift'] <= spec['max_abs_drift']
        if 'time_column' in spec:
            values = [r[columns.index(spec['time_column'])] for r in rows]
            item['time_monotonic'] = all(b >= a for a, b in zip(values, values[1:]))
            item['physical_time'] = [values[0], values[-1]]
            passed = passed and item['time_monotonic']
        measured.append(item)
        previous_index, previous_generation = index, block['phase']['generation']
    return dict(blocks=measured, checked_blocks=len(chosen), total_blocks=len(blocks)), passed


def required(spec):
    return spec.get('role', 'required') == 'required'


def measurement_identity(spec):
    identity = {k: v for k, v in spec.items() if k not in (
        'min_rows', 'min_frames', 'min_bytes', 'max_abs_drift', 'tolerance', 'expected',
        'unit', 'basis', 'bounds', 'values', 'role', 'action')}
    for field in ('bounds', 'values'):
        if field in spec:
            identity[field] = sorted(spec[field])
    return encode(identity)


def run_checks(store, task_id, checks, artifacts=(), plan_id=None, component=None, operation_id=None, compute_runtime=None):
    validate_checks(checks)
    receipt_id = 'check_' + operation_id if operation_id else identifier('check')
    with store.connect() as db:
        existing = db.execute('SELECT result FROM check_receipts WHERE id=?', (receipt_id,)).fetchone()
        if existing:
            return json.loads(existing[0])
    paths = set(artifacts) | {c['path'] for c in checks}
    sources = {p: file_hash(store.path(task_id, p)) for p in paths if store.path(task_id, p).is_file()}
    results = []
    for check in checks:
        try:
            measured, passed = execute_check(store.path(task_id, check['path']), check)
            results.append(dict(specification=check, measured=measured, passed=bool(passed)))
        except (Fault, ValueError, IndexError, KeyError, OSError) as exc:
            results.append(dict(specification=check, passed=False, error=str(exc)))
    current = len(sources) == len(paths) and evidence_current(store, task_id, sources)
    obligations = [r for r in results if required(r['specification'])]
    passed = current and bool(obligations) and all(r['passed'] for r in obligations)
    value = dict(id=receipt_id, task_id=task_id, plan_id=plan_id, component=component,
                 checker_version=CHECK_VERSION, checks=results, passed=passed, sources=sources,
                 artifacts=list(artifacts), evidence_current=current, created=time.time(), compute_runtime=compute_runtime,
                 candidate_only=not obligations, advisory=[r for r in results if not required(r['specification'])], phase_checker_version=1)
    with store.transaction() as db:
        db.execute('INSERT INTO check_receipts VALUES(?,?,?,?,?,?,?,?,?)',
                   (value['id'], task_id, plan_id, component, encode(checks), encode(value), encode(sources), int(passed), value['created']))
        store.event(db, task_id, 'validation_checked', dict(receipt_id=value['id'], component=component, passed=passed))
        if not passed:
            affected = []
            failed_paths = {c['specification']['path'] for c in obligations if not c['passed']}
            latest = {r['path']: r for r in db.execute('SELECT * FROM artifacts WHERE task_id=? ORDER BY created,rowid', (task_id,))}
            for artifact in latest.values():
                if not failed_paths.intersection({artifact['path'], *json.loads(artifact['sources'])}):
                    continue
                if any(c.get('receipt_id') and not receipt_current(store, db, task_id, c['receipt_id'], artifact['path'])
                       for c in json.loads(artifact['checks'])):
                    affected.append(artifact['id'])
            if affected:
                changed = db.execute("UPDATE tasks SET state='active' WHERE id=? AND state='completed'", (task_id,)).rowcount
                if changed:
                    store.bump(db, task_id)
                store.event(db, task_id, 'measured_review_failed', dict(receipt_id=value['id'], artifacts=affected))
        if plan_id and passed:
            task = store.task(db, task_id)
            save_memory(db, dict(id='memory_' + value['id'], task_id=task_id, kind='validation', status='verified',
                                 title=task['title'] + ' / ' + str(component), summary='服务执行检查通过：' + str(component),
                                 conditions=task['constraints_json'], evidence=dict(receipt_id=value['id'], plan_id=plan_id, files=sources), source_event=None),
                        'service_executed_check')
    return value


def receipt_current(store, db, task_id, receipt_id, artifact=None):
    row = db.execute('SELECT * FROM check_receipts WHERE id=? AND task_id=?', (receipt_id, task_id)).fetchone()
    if not row:
        return False
    sources = json.loads(row['sources'])
    if row['plan_id']:
        plan = db.execute('SELECT active FROM validation_plans WHERE id=?', (row['plan_id'],)).fetchone()
        if not plan or not plan[0]:
            return False
        suspended = db.execute('SELECT value FROM settings WHERE key=?', ('validation_suspended:' + row['plan_id'],)).fetchone()
        if suspended and row['component'] in json.loads(suspended[0]):
            return False
        latest = db.execute('SELECT passed FROM check_receipts WHERE plan_id=? AND component=? ORDER BY created DESC,rowid DESC LIMIT 1', (row['plan_id'], row['component'])).fetchone()
        if latest and not latest[0]:
            return False
    if json.loads(row['result']).get('checker_version') != CHECK_VERSION:
        return False
    result = json.loads(row['result'])
    if not result.get('phase_checker_version') and any(c['specification'].get('kind') == 'thermo'
            and 'max_abs_drift' in c['specification'] for c in result.get('checks', [])):
        return False
    # Keep the latest scientific verdict for each measured quantity and source version.
    # A file-existence check cannot clear a later failed numerical check.
    original_checks = json.loads(row['result']).get('checks', [])
    identities = {measurement_identity(c['specification']) for c in original_checks if required(c['specification'])}
    latest_reviews = {}
    for review in db.execute('SELECT sources,result FROM check_receipts WHERE task_id=? AND created>=? ORDER BY created,rowid',
                             (task_id, row['created'])):
        hashes, result = json.loads(review['sources']), json.loads(review['result'])
        for check in result.get('checks', []):
            spec = check['specification']
            if not required(spec) or measurement_identity(spec) not in identities:
                continue
            path = spec['path']
            if path not in sources or hashes.get(path) != sources[path]:
                continue
            # Threshold amendments keep the same measurement identity.
            latest_reviews[measurement_identity(spec)] = check['passed']
    if not all(latest_reviews.values()):
        return False
    previous = json.loads(row['result']).get('reused_from')
    if previous:
        original = db.execute('SELECT task_id FROM check_receipts WHERE id=?', (previous,)).fetchone()
        if not original or not receipt_current(store, db, original[0], previous):
            return False
    return bool(row['passed'] and (artifact is None or artifact in sources)
                and evidence_current(store, task_id, sources))
