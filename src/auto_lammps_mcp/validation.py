"""Match immutable native inputs and measured component evidence before dispatch."""
from __future__ import annotations

import json
import math
import re

from .core import digest, encode, require
from .research_store import allowed, evidence_current
from .files import file_hash

SCOPES = {'static', 'environment', 'numerics', 'outputs', 'analysis'}


def snapshot_files(store, db, task_id, snapshot_id):
    row = db.execute('SELECT manifest FROM snapshots WHERE id=? AND task_id=?', (snapshot_id, task_id)).fetchone()
    require(row, '输入快照不存在')
    return json.loads(row[0])


def normalized_files(store, files, variations):
    normalized, parameters = {}, {}
    for path, info in files.items():
        blob = store.home / 'blobs' / info['sha256']
        require(file_hash(blob) == info['sha256'], '输入快照摘要不一致')
        selected = [v for v in variations if v['path'] == path]
        sha = info['sha256']
        if selected:
            require(blob.stat().st_size <= 2 * 1024 * 1024, '种子变量文件应为原生文本输入')
            text = blob.read_text()
            for spec in selected:
                variable = spec['variable']
                pattern = re.compile(r'(?m)^(\s*variable\s+' + re.escape(variable) + r'\s+(?:index|equal)\s+)([0-9]+)(\s*(?:#.*)?)$')
                matches = list(pattern.finditer(text))
                require(len(matches) == 1, '允许变化的种子必须是唯一的原生整数 variable 定义: ' + variable)
                value = int(matches[0][2])
                require(spec['min'] <= value <= spec['max'], '随机种子超出已登记范围')
                parameters[path + ':' + variable] = value
                text = pattern.sub(lambda m: m[1] + '<validated-seed>' + m[3], text)
            sha = digest(text.encode())
        normalized[path] = dict(sha256=sha, executable=bool(info.get('executable', False)))
    return normalized, parameters


def environment(store):
    cluster = store.setting('cluster', {})
    # Connection credentials and output locations do not determine numerical equivalence.
    return {key: cluster.get(key) for key in ('environment_script',)}


def match_plan(store, db, task_id, snapshot_id, plan_id, conditions=None):
    task = store.task(db, task_id)
    row = db.execute('SELECT * FROM validation_plans WHERE id=?', (plan_id,)).fetchone()
    require(row and allowed(db, task_id, row['task_id']), '验证方案不存在或不在当前研究的访问范围')
    spec = json.loads(row['specification'])
    suspended_row = db.execute('SELECT value FROM settings WHERE key=?', ('validation_suspended:' + plan_id,)).fetchone()
    suspended = json.loads(suspended_row[0]) if suspended_row else []
    current = snapshot_files(store, db, task_id, snapshot_id)
    normalized, parameters = normalized_files(store, current, spec['variations'])
    conditions = task['constraints_json'] if conditions is None else conditions
    components, used = [], []
    # Extra project outputs are harmless; a new executable/input dependency requires a new plan.
    new_inputs = [p for p in current if p not in spec['snapshot_paths'] and
                  (p.endswith(('.sh', '.in', '.lmp', '.data', '.eam', '.alloy', '.fs')) or p.startswith('in.'))]
    for component in spec['components']:
        differences = []
        if not row['active']:
            differences.append('plan_disabled')
        if component['id'] in suspended:
            differences.append('component_suspended')
        differences.extend('new_condition:' + key for key in conditions if key not in spec['conditions'])
        for path in component['files']:
            if normalized.get(path) != spec['inputs'].get(path):
                differences.append('file:' + path)
        for key in component['condition_keys']:
            if conditions.get(key) != spec['conditions'].get(key):
                differences.append('condition:' + key)
        if environment(store) != spec['environment'] and component['scope'] in ('environment', 'numerics', 'outputs'):
            differences.append('environment')
        if new_inputs:
            differences.extend('new_input:' + x for x in new_inputs)
        receipt = db.execute('SELECT * FROM check_receipts WHERE plan_id=? AND component=? ORDER BY created DESC,rowid DESC LIMIT 1',
                             (plan_id, component['id'])).fetchone()
        if not receipt:
            differences.append('missing_measurement')
        elif not receipt['passed']:
            differences.append('failed_measurement')
        else:
            from .verification import receipt_current
            valid_receipt = receipt_current(store, db, receipt['task_id'], receipt['id'])
            if not valid_receipt:
                differences.append('evidence_changed')
        ready = not differences
        if ready:
            used.append(receipt['id'])
        components.append(dict(id=component['id'], scope=component['scope'], ready=ready,
                               differences=differences, receipt_id=receipt['id'] if receipt else None))
    ready = bool(components) and all(c['ready'] for c in components)
    return dict(plan_id=plan_id, source_task_id=row['task_id'], ready=ready,
                reuse='direct' if ready else 'partial' if any(c['ready'] for c in components) else 'needs_validation',
                components=components, parameters=parameters, receipt_ids=used,
                missing=[c['id'] for c in components if not c['ready']],
                next_action='正式计算或批量重复' if ready else '只补 missing 列出的检查')


def reserve_budget(store, db, plan_id, run_id, requested):
    row = db.execute('SELECT specification,task_id FROM validation_plans WHERE id=?', (plan_id,)).fetchone()
    limit = min(json.loads(row['specification'])['budget_core_hours'], store.task(db, row['task_id'])['policy'].get('preflight_core_hours', 100))
    used = 0.0
    for run in db.execute("SELECT r.id,r.state,r.evidence,v.reserved_core_hours FROM runs r JOIN run_validations v ON v.run_id=r.id WHERE r.task_id=? AND v.stage='preflight' AND r.id!=?", (row['task_id'], run_id)):
        if run['state'] in ('prepared', 'cancelled_before_dispatch', 'staging_failed'):
            continue
        evidence = json.loads(run['evidence'])
        accounting = evidence.get('accounting', evidence)
        seconds = accounting.get('core_seconds') if run['state'] in ('completed', 'failed', 'cancelled', 'timeout') else None
        used += float(seconds) / 3600 if seconds is not None else run['reserved_core_hours']
    require(math.isfinite(requested) and requested > 0 and used + requested <= limit + 1e-10,
            f'预验证预算不足：已用及保留 {used:.3f}，本次 {requested:.3f}，总额 {limit:.3f} 核时',
            'preflight_budget', '保留证据，缩短本次验证或由用户调整验证方案预算')
    db.execute('UPDATE run_validations SET reserved_core_hours=? WHERE run_id=?', (requested, run_id))


def dispatch_gate(store, db, run):
    binding = db.execute('SELECT * FROM run_validations WHERE run_id=?', (run['id'],)).fetchone()
    if not binding:
        require(run['purpose'] == 'environment_check', '正式计算需要关联验证方案', 'validation_required', '调用 validation_plan 或复用已有验证')
        return None
    match = match_plan(store, db, run['task_id'], run['snapshot_id'], binding['plan_id'])
    if binding['stage'] == 'production':
        require(match['ready'], '当前输入存在未完成的验证项', 'validation_required', match['next_action'])
        scopes = {c['scope'] for c in match['components']}
        require({'static', 'environment', 'numerics', 'outputs'} <= scopes, '正式计算需要文件、环境、数值与输出验证',
                'validation_required', '补齐对应验证组件')
    else:
        plan = db.execute('SELECT active,specification FROM validation_plans WHERE id=?', (binding['plan_id'],)).fetchone()
        require(plan['active'], '验证方案已停用')
        # Preflight may lack measurements but must use the fixed input and conditions.
        forbidden = [d for c in match['components'] for d in c['differences']
                     if d.startswith(('file:', 'condition:', 'environment', 'new_input:', 'new_condition:', 'plan_disabled'))]
        require(not forbidden, '预验证输入已变化，请建立对应的新方案', 'validation_changed', ', '.join(forbidden))
        rules = json.loads(run['rules']) if isinstance(run['rules'], str) else run['rules']
        reserve_budget(store, db, binding['plan_id'], run['id'], rules['cores'] * rules['wall_seconds'] / 3600)
    db.execute('UPDATE run_validations SET coverage=? WHERE run_id=?', (encode(match), run['id']))
    return match
