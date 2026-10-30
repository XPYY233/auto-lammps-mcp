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
    return {key: cluster.get(key) for key in ('environment_script', 'partition')}


def match_plan(store, db, task_id, snapshot_id, plan_id, conditions=None, declared_components=None):
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
    # Snapshots explicitly select research inputs. Unknown extensions may be
    # executable helpers or binary models, so none are silently ignored.
    new_inputs = sorted(set(current) - set(spec['snapshot_paths']))
    for component in spec['components']:
        differences = []
        if not row['active']:
            differences.append('plan_disabled')
        if component['id'] in suspended:
            differences.append('component_suspended')
        if declared_components is None:
            differences.extend('new_condition:' + key for key in conditions if key not in spec['conditions'])
        for path in component['files']:
            if normalized.get(path) != spec['inputs'].get(path):
                differences.append('file:' + path)
        for key in component['condition_keys']:
            if conditions.get(key) != spec['conditions'].get(key):
                differences.append('condition:' + key)
        if environment(store) != spec['environment'] and component['scope'] in ('environment', 'numerics', 'outputs'):
            differences.append('environment')
        if component['scope'] in ('environment', 'numerics', 'outputs'):
            from .runtime_evidence import current as current_runtime
            runtime = spec.get('runtime')
            latest = current_runtime(store, runtime) if runtime else None
            if not runtime or not latest:
                differences.append('runtime_missing')
            elif not latest.get('identity'):
                differences.append('runtime_changed')
            else:
                from .runtime_probe import program_identity
                if program_identity(latest['identity']) != program_identity(runtime['identity']):
                    differences.append('runtime_changed')
        if new_inputs and declared_components is None:
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
            if component['scope'] in ('environment', 'numerics', 'outputs'):
                node = json.loads(receipt['result']).get('compute_runtime')
                latest_node = current_runtime(store, runtime, role='compute') if runtime else None
                if not node:
                    differences.append('runtime_compute_missing')
                elif latest_node and latest_node.get('fingerprint') != node['fingerprint']:
                    differences.append('runtime_compute_changed')
        ready = not differences
        if ready:
            used.append(receipt['id'])
        components.append(dict(id=component['id'], scope=component['scope'], ready=ready, required=component.get('required', True),
                               differences=differences, receipt_id=receipt['id'] if receipt else None))
    required_components = [c for c in components if c['required']]
    ready = bool(required_components) and all(c['ready'] for c in required_components)
    return dict(plan_id=plan_id, source_task_id=row['task_id'], ready=ready,
                runtime_observed_at=spec.get('runtime', {}).get('observed_at'), runtime_rechecked_before_dispatch=True,
                reuse='direct' if ready else 'partial' if any(c['ready'] for c in components) else 'needs_validation',
                components=components, parameters=parameters, receipt_ids=used,
                missing=[c['id'] for c in components if c['required'] and not c['ready']],
                advisory_missing=[c['id'] for c in components if not c['required'] and not c['ready']],
                next_action='正式计算或批量重复' if ready else '只补 missing 列出的检查')


def confirmed_budget(task):
    policy = task['policy']
    limit = policy.get('preflight_core_hours')
    require(type(limit) in (int, float) and math.isfinite(limit) and limit > 0
            and policy.get('preflight_budget_authorization'),
            '尚未记录用户确认的预验证预算；估算和任务条件不能代替预算授权',
            'preflight_budget_confirmation',
            '保存 policy.preflight_core_hours 与 preflight_budget_authorization；100 核时是建议值')
    return limit


def reserve_budget(store, db, plan_id, run_id, requested):
    row = db.execute('SELECT specification,task_id FROM validation_plans WHERE id=?', (plan_id,)).fetchone()
    # Charge the requesting task, including when it reuses another task's plan.
    task_id = db.execute('SELECT task_id FROM runs WHERE id=?', (run_id,)).fetchone()[0]
    limit = min(json.loads(row['specification'])['budget_core_hours'], confirmed_budget(store.task(db, task_id)))
    used = 0.0
    for run in db.execute("SELECT r.id,r.state,r.evidence,v.reserved_core_hours FROM runs r JOIN run_validations v ON v.run_id=r.id WHERE r.task_id=? AND v.stage='preflight' AND r.id!=?", (task_id, run_id)):
        if run['state'] in ('prepared', 'cancelled_before_dispatch', 'staging_failed'):
            continue
        evidence = json.loads(run['evidence'])
        accounting = evidence.get('accounting', evidence)
        seconds = accounting.get('core_seconds') if run['state'] in ('completed', 'failed', 'cancelled', 'timeout') else None
        used += float(seconds) / 3600 if seconds is not None else run['reserved_core_hours']
    require(math.isfinite(requested) and requested > 0 and used + requested <= limit + 1e-10,
            f'预验证预算不足：已用及保留 {used:.3f}，本次 {requested:.3f}，总额 {limit:.3f} 核时',
            'preflight_budget', '调度预留=核数×最长运行时限，与预计用量分开；按实际验证工作量缩短时限，扩大预算须保存新的用户确认')
    db.execute('UPDATE run_validations SET reserved_core_hours=? WHERE run_id=?', (requested, run_id))


def dispatch_gate(store, db, run):
    binding = db.execute('SELECT * FROM run_validations WHERE run_id=?', (run['id'],)).fetchone()
    if not binding:
        require(run['purpose'] == 'environment_check', '正式计算需要关联验证方案', 'validation_required', '调用 validation_plan 或复用已有验证')
        return None
    match = match_plan(store, db, run['task_id'], run['snapshot_id'], binding['plan_id'])
    actual_env = json.loads(run['environment']) if isinstance(run['environment'], str) else run['environment']
    require(actual_env['environment_script'] == environment(store)['environment_script'],
            '已准备作业的启动环境与当前配置不一致', 'validation_changed', '保留原作业，按新环境准备新执行意图')
    if binding['stage'] == 'production':
        require(match['ready'], '当前输入存在未完成的验证项', 'validation_required', match['next_action'])
        scopes = {c['scope'] for c in match['components'] if c['required'] and c['ready']}
        require({'static', 'environment', 'numerics', 'outputs'} <= scopes, '正式计算需要文件、环境、数值与输出验证',
                'validation_required', '补齐对应验证组件')
    else:
        plan = db.execute('SELECT active,specification FROM validation_plans WHERE id=?', (binding['plan_id'],)).fetchone()
        require(plan['active'], '验证方案已停用')
        # Preflight may lack measurements but must use the fixed input and conditions.
        forbidden = [d for c in match['components'] for d in c['differences']
                     if d.startswith(('file:', 'condition:', 'environment', 'runtime', 'new_input:', 'new_condition:', 'plan_disabled'))]
        require(not forbidden, '预验证输入已变化，请建立对应的新方案', 'validation_changed', ', '.join(forbidden))
        rules = json.loads(run['rules']) if isinstance(run['rules'], str) else run['rules']
        reserve_budget(store, db, binding['plan_id'], run['id'], rules['cores'] * rules['wall_seconds'] / 3600)
    db.execute('UPDATE run_validations SET coverage=? WHERE run_id=?', (encode(match), run['id']))
    return match
