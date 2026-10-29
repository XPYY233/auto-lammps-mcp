"""Public research memory, measured validation and repeat-study operations."""
from __future__ import annotations

import json
import math
import re
import time

from .core import digest, encode, identifier, require
from .files import atomic_write, file_hash
from .research_store import allowed, memory_value, save_memory, search_memory, stage_context
from .validation import SCOPES, dispatch_gate, environment, match_plan, normalized_files, snapshot_files
from .verification import receipt_current, validate_checks


def public(fn):
    fn.public = True
    return fn


class ResearchOperations:
    @public
    def checker_register(self, task_id: str, revision: int, name: str, cases: list[dict], criteria: dict,
                         op_id: str, token: str | None = None) -> dict:
        """Qualify a native checker after analysis_submit ran independent positive and negative fixtures. cases: [{operation_id,output,expected_passed}]. criteria is one JSON values/bounds check; its path is supplied from each actual output. Freeze script hash, fixtures, expected results and criteria; all cases must match."""
        require(name.strip() and len(cases) >= 2 and {c.get('expected_passed') for c in cases} == {True, False}, '检查程序需要独立正例和反例')
        require(criteria.get('kind') == 'json', '检查程序输出结构化实测 JSON')
        validate_checks([{**criteria, 'path': 'result.json'}])
        def apply(db):
            self.store.task(db, task_id, revision, token)
            from .checkers import analysis_evidence
            from .verification import execute_check
            receipts, scripts, fixtures = [], set(), set()
            sources = {}
            for case in cases:
                require(set(case) == {'operation_id', 'output', 'expected_passed'} and type(case['expected_passed']) is bool, '样例字段无效')
                item = analysis_evidence(self.store, db, task_id, case['operation_id'], case['output'])
                scripts.add(item['script_sha256'])
                fixtures.add(digest(encode(sorted(v for k, v in item['sources'].items() if k != item['script'])).encode()))
                measured, passed = execute_check(self.store.path(task_id, item['path']), criteria)
                require(passed == case['expected_passed'], '检查程序未识别预定正反样例')
                sources[item['path']] = file_hash(self.store.path(task_id, item['path']))
                receipts.append(dict(**case, measured=measured, passed=passed, path=item['path']))
            require(len(scripts) == 1 and len(fixtures) >= 2, '样例必须使用相同程序和不同输入')
            key = identifier('checker')
            value = dict(task_id=task_id, name=name, script_sha256=scripts.pop(), criteria=criteria, cases=receipts, sources=sources)
            self.store.set_setting(db, key, value)
            save_memory(db, dict(id='memory_' + key, task_id=task_id, kind='method', status='verified', title=name,
                summary='原生检查程序通过独立正反样例', conditions={}, evidence=dict(checker_id=key, files=sources), source_event=None), 'checker_qualified')
            self.store.bump(db, task_id)
            self.store.event(db, task_id, 'checker_qualified', dict(checker_id=key, cases=len(cases)))
            return dict(checker_id=key, revision=revision+1, **value)
        return self.store.mutate(op_id, dict(method='checker_register', task_id=task_id, revision=revision, name=name, cases=cases, criteria=criteria, token=token), apply)

    @public
    def checker_check(self, task_id: str, revision: int, checker_id: str, operation_id: str,
                      output: str, op_id: str, artifacts: list[str] | None = None, token: str | None = None) -> dict:
        """Check a completed analysis using a qualified program's fixed criteria and exact script version. Queue a service receipt for the measured output and companion artifacts."""
        def apply(db):
            self.store.task(db, task_id, revision, token)
            from .checkers import analysis_evidence
            from .research_store import evidence_current
            row = db.execute('SELECT value FROM settings WHERE key=?', (checker_id,)).fetchone()
            require(row and checker_id.startswith('checker_'), '检查程序不存在')
            value = json.loads(row[0])
            require(allowed(db, task_id, value['task_id']) and evidence_current(self.store, value['task_id'], value['sources']), '检查程序样例证据失效或不可访问')
            item = analysis_evidence(self.store, db, task_id, operation_id, output)
            require(item['script_sha256'] == value['script_sha256'], '检查程序已变化，重新通过正反样例')
            self.store.bump(db, task_id)
            return self.store.queue(db, task_id, 'check', dict(checks=[{**value['criteria'], 'path': item['path']}], artifacts=artifacts or []))
        return self.store.mutate(op_id, dict(method='checker_check', task_id=task_id, revision=revision, checker_id=checker_id, operation_id=operation_id, output=output, artifacts=artifacts, token=token), apply)

    @public
    def memory_rebuild(self, task_id: str, op_id: str) -> dict:
        """Re-index retained task events into observed memories. Existing corrections and disabled records are preserved. No historical claim becomes verified."""
        def apply(db):
            self.store.task(db, task_id)
            from .research_store import remember_event
            count = 0
            for row in db.execute('SELECT * FROM events WHERE task_id=? ORDER BY seq', (task_id,)).fetchall():
                if not db.execute('SELECT 1 FROM memories WHERE id=?', ('memory_event_' + str(row['seq']),)).fetchone():
                    remember_event(db, task_id, row['kind'], json.loads(row['data']), row['seq'])
                    count += db.execute('SELECT count(*) FROM memories WHERE id=?', ('memory_event_' + str(row['seq']),)).fetchone()[0]
            return dict(task_id=task_id, indexed=count)
        return self.store.mutate(op_id, dict(method='memory_rebuild', task_id=task_id), apply)

    @public
    def research_metrics(self, task_id: str) -> dict:
        """Read study denominators, preflight/production counts and accounted costs. Unknown accounting remains explicit. Tool/model/human-intervention totals require the host's original trace."""
        with self.store.connect() as db:
            task = self.store.task(db, task_id)
            rows = db.execute('SELECT r.*,v.stage FROM runs r LEFT JOIN run_validations v ON v.run_id=r.id WHERE r.task_id=? ORDER BY r.created', (task_id,)).fetchall()
            groups = {}
            for row in rows:
                group = groups.setdefault(row['stage'] or 'historical', dict(prepared=0, dispatched=0, failed=0, accounted_core_hours=0, accounting_unknown=0))
                group['prepared'] += 1
                group['dispatched'] += bool(row['job_id'] or row['state'] in ('dispatching', 'unknown'))
                group['failed'] += row['state'] in ('failed', 'timeout', 'cancelled')
                seconds = json.loads(row['evidence']).get('core_seconds')
                if seconds is None and row['job_id']:
                    group['accounting_unknown'] += 1
                elif seconds is not None:
                    group['accounted_core_hours'] += seconds / 3600
            first = db.execute("SELECT min(created) FROM events WHERE task_id=? AND kind='dispatch_started'", (task_id,)).fetchone()[0]
            completed = db.execute("SELECT 1 FROM events WHERE task_id=? AND kind='research_completed'", (task_id,)).fetchone()
            return dict(task_id=task_id, initiated_studies=1, groups=groups, current_state=task['state'],
                        completion_recorded=bool(completed and task['state'] == 'completed'),
                        historical_completion_recorded=bool(completed), seconds_to_first_dispatch=first-task['created'] if first else None,
                        model_calls=None, tool_calls=None, human_interventions=None, host_trace_required=True)

    @public
    def memory_search(self, task_id: str, query: str = '', limit: int = 5) -> dict:
        """Find local research experience across permitted projects using terms, aliases and conditions. Similarity selects evidence; validation_match decides reuse. No new computation occurs."""
        with self.store.connect() as db:
            self.store.task(db, task_id)
            return search_memory(self.store, db, task_id, query, limit)

    @public
    def memory_get(self, task_id: str, memory_id: str) -> dict:
        """Read one memory and its correction history with source task/event/file references."""
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone()
            require(row and allowed(db, task_id, row['task_id']), '记忆不存在或不在当前研究访问范围')
            return dict(memory=memory_value(row), history=[json.loads(r[0]) for r in db.execute(
                'SELECT record FROM memory_versions WHERE id=? ORDER BY revision', (memory_id,))])

    @public
    def memory_record(self, task_id: str, revision: int, title: str, summary: str, kind: str,
                      op_id: str, conditions: dict | None = None, files: list[str] | None = None,
                      memory_id: str | None = None, action: str = 'record', reason: str = '', token: str | None = None) -> dict:
        """Save a candidate method/failure/preference, correct it, or disable it. Service measurements create verified memories. action: record, correct, disable. Disabling a validation memory also suspends reuse of that plan."""
        require(kind in ('method', 'failure', 'preference', 'result'), '记忆类型应为 method/failure/preference/result')
        require(action in ('record', 'correct', 'disable') and (action == 'disable' or title.strip() and summary.strip()), '记忆动作或内容无效')
        def apply(db):
            self.store.task(db, task_id, revision, token)
            previous = db.execute('SELECT * FROM memories WHERE id=?', (memory_id,)).fetchone() if memory_id else None
            require(action == 'record' or previous, '更正或停用需要已有记忆')
            if previous:
                require(allowed(db, task_id, previous['task_id']), '记忆不在当前研究访问范围')
            sources = {}
            for name in files or []:
                path = self.store.path(task_id, name)
                require(path.is_file(), '记忆来源文件不存在: ' + name)
                sources[name] = file_hash(path)
            value = memory_value(previous) if previous else dict(id=identifier('memory'), task_id=task_id, source_event=None)
            old_plan = value.get('evidence', {}).get('plan_id')
            if action == 'disable':
                value['status'] = 'disabled'
            else:
                value.update(task_id=task_id, title=title, summary=summary, kind=kind, status='candidate',
                             conditions=conditions or {}, evidence=dict(files=sources))
            if old_plan:
                db.execute('UPDATE validation_plans SET active=0 WHERE id=?', (old_plan,))
            result = save_memory(db, value, reason or action)
            self.store.bump(db, task_id)
            self.store.event(db, task_id, 'memory_updated', dict(memory_id=result['id'], action=action))
            return dict(memory=result, revision=revision + 1)
        return self.store.mutate(op_id, dict(method='memory_record', task_id=task_id, revision=revision, title=title,
            summary=summary, kind=kind, conditions=conditions, files=files, memory_id=memory_id, action=action, reason=reason, token=token), apply)

    @public
    def research_context(self, task_id: str) -> dict:
        """Read stage guidance, local memories and confirmed conditions before choosing resources, editing scripts, preparing runs or recovering. Ask only unresolved scientific choices. Save pause before further actions when the user asks to discuss."""
        with self.store.connect() as db:
            return stage_context(self.store, db, task_id)

    @public
    def check_run(self, task_id: str, revision: int, checks: list[dict], op_id: str,
                  artifacts: list[str] | None = None, token: str | None = None) -> dict:
        """Queue actual file checks; service computes verdict and hashes. Each has kind,path, optional unit,basis. file: min_bytes,contains; csv: columns,min_rows,bounds={column:{min,max}}; json: values={field:expected},bounds; thermo: block,columns,min_rows,energy_column,max_abs_drift,time_column; trajectory: columns,min_frames,atoms; energy_jump: before_block,after_block,column,expected,tolerance. artifacts binds companion files. Read operation_get then check_get. No physical evaluation."""
        validate_checks(checks)
        for path in [c['path'] for c in checks] + (artifacts or []):
            self.store.path(task_id, path)
        def apply(db):
            self.store.task(db, task_id, revision, token)
            self.store.bump(db, task_id)
            return self.store.queue(db, task_id, 'check', dict(checks=checks, artifacts=artifacts or []))
        return self.store.mutate(op_id, dict(method='check_run', task_id=task_id, revision=revision,
                                            checks=checks, artifacts=artifacts, token=token), apply)

    @public
    def check_get(self, task_id: str, receipt_id: str) -> dict:
        """Read service-measured values, fixed criteria, coverage and current source integrity."""
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM check_receipts WHERE id=?', (receipt_id,)).fetchone()
            require(row and allowed(db, task_id, row['task_id']), '检查回执不存在或不可访问')
            return {**json.loads(row['result']), 'current_passed': receipt_current(self.store, db, row['task_id'], receipt_id)}

    @public
    def validation_plan(self, task_id: str, revision: int, snapshot_id: str, entry: str,
                        components: list[dict], op_id: str, variations: list[dict] | None = None,
                        budget_core_hours: float = 100, reuse_from: str | None = None, token: str | None = None,
                        monitor: list[dict] | None = None) -> dict:
        """Freeze native inputs and checks before preflight. Each component has id, scope (static/environment/numerics/outputs/analysis), files, condition_keys and checks. Conditions come from current task. variations permits explicit integer seed variables: path,variable,min,max. reuse_from copies only compatible measured components. Production requires static/environment/numerics/outputs. All physical checks use run_prepare(stage='preflight',validation_id=...)."""
        require(math.isfinite(budget_core_hours) and budget_core_hours > 0, '预验证预算应为正数')
        require(components and len({c.get('id') for c in components}) == len(components), '验证组件需要唯一 id')
        variations = variations or []
        if monitor:
            validate_checks(monitor)
            require(all(c['kind'] == 'thermo' and set(c) <= {'kind', 'path', 'unit', 'block', 'energy_column', 'max_abs_drift'} for c in monitor), '节点监视使用 thermo 的分段能量漂移检查')
        require(len({(v.get('path'), v.get('variable')) for v in variations}) == len(variations), '种子定义重复')
        for v in variations:
            require(set(v) == {'path', 'variable', 'min', 'max'} and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', v['variable']), '种子定义需要 path/variable/min/max')
            require(type(v['min']) is int and type(v['max']) is int and 0 < v['min'] <= v['max'] <= 2147483647, '种子范围无效')
        def apply(db):
            task = self.store.task(db, task_id, revision, token)
            files = snapshot_files(self.store, db, task_id, snapshot_id)
            require(budget_core_hours <= task['policy'].get('preflight_core_hours', 100), '超出当前预验证预算；用户调整后保存 policy.preflight_core_hours')
            require(entry in files, '研究入口不在快照中')
            normalized, parameters = normalized_files(self.store, files, variations)
            inputs = set()
            for c in components:
                require(set(c) == {'id', 'scope', 'files', 'condition_keys', 'checks'} and c['id'] and c['scope'] in SCOPES, '验证组件字段无效')
                require(c['files'] and set(c['files']) <= set(files), '组件需要真实快照输入')
                require(set(c['condition_keys']) <= set(task['constraints_json']), '条件字段不在当前任务中')
                validate_checks(c['checks'])
                for check in c['checks']:
                    self.store.path(task_id, check['path'])
                inputs.update(c['files'])
            require(entry in inputs and all(v['path'] in inputs for v in variations), '入口和种子文件必须进入验证依赖')
            require(inputs == set(files), '验证快照只包含研究输入，并将全部文件列入组件依赖；使用 project_snapshot(paths=...) 排除结果文件')
            require(set().union(*(set(c['condition_keys']) for c in components)) == set(task['constraints_json']), '每项科学条件都需要关联至少一个验证组件')
            spec = dict(entry=entry, conditions=task['constraints_json'], components=components, variations=variations,
                        inputs={p: normalized[p] for p in sorted(inputs)}, snapshot_paths=sorted(files),
                        environment=environment(self.store), budget_core_hours=budget_core_hours, monitor=monitor or [])
            pid = identifier('validation')
            db.execute('INSERT INTO validation_plans VALUES(?,?,?,?,?,1,?)',
                       (pid, task_id, snapshot_id, encode(spec), digest(encode(spec).encode()), time.time()))
            reused = []
            if reuse_from:
                match = match_plan(self.store, db, task_id, snapshot_id, reuse_from)
                source = json.loads(db.execute('SELECT specification FROM validation_plans WHERE id=?', (reuse_from,)).fetchone()[0])
                previous = {c['id']: c for c in source['components']}
                for c in components:
                    old = next((x for x in match['components'] if x['id'] == c['id'] and x['ready']), None)
                    if old and previous[c['id']] == c and source['variations'] == variations and source.get('monitor', []) == (monitor or []):
                        row = db.execute('SELECT * FROM check_receipts WHERE id=?', (old['receipt_id'],)).fetchone()
                        rid = identifier('check')
                        value = {**json.loads(row['result']), 'id': rid, 'plan_id': pid, 'reused_from': row['id']}
                        db.execute('INSERT INTO check_receipts VALUES(?,?,?,?,?,?,?,?,?)',
                                   (rid, row['task_id'], pid, c['id'], row['specification'], encode(value), row['sources'], row['passed'], time.time()))
                        reused.append(c['id'])
            self.store.bump(db, task_id)
            self.store.event(db, task_id, 'validation_planned', dict(plan_id=pid, reused=reused))
            return dict(plan_id=pid, revision=revision + 1, reused=reused, specification=spec)
        return self.store.mutate(op_id, dict(method='validation_plan', task_id=task_id, revision=revision, snapshot_id=snapshot_id,
            entry=entry, components=components, variations=variations, budget_core_hours=budget_core_hours, reuse_from=reuse_from, token=token, monitor=monitor), apply)

    @public
    def validation_match(self, task_id: str, snapshot_id: str, plan_id: str) -> dict:
        """Compare actual input dependencies, conditions, environment and current measurements. Returns direct/partial/needs_validation with exact missing components. Matching does not run a simulation."""
        with self.store.connect() as db:
            return match_plan(self.store, db, task_id, snapshot_id, plan_id)

    @public
    def validation_control(self, task_id: str, revision: int, plan_id: str, reason: str, op_id: str, token: str | None = None, components: list[str] | None = None) -> dict:
        """Suspend reuse of an invalid plan and its pending batches, retaining running evidence and costs. Corrected settings get a new plan with reuse_from for still valid components."""
        require(reason.strip(), '保存新证据或共同缺陷原因')
        def apply(db):
            self.store.task(db, task_id, revision, token)
            row = db.execute('SELECT * FROM validation_plans WHERE id=? AND task_id=?', (plan_id, task_id)).fetchone()
            require(row, '该验证方案不属于当前研究')
            if components is None:
                db.execute('UPDATE validation_plans SET active=0 WHERE id=?', (plan_id,))
            else:
                require(components and set(components) <= {c['id'] for c in json.loads(row['specification'])['components']}, '请选择该方案已有组件')
                key = 'validation_suspended:' + plan_id
                previous = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                self.store.set_setting(db, key, sorted(set(components) | set(json.loads(previous[0]) if previous else [])))
            db.execute("UPDATE batches SET state='paused' WHERE plan_id=? AND state='active'", (plan_id,))
            affected = [r[0] for r in db.execute('SELECT DISTINCT r.task_id FROM runs r JOIN run_validations v ON v.run_id=r.id WHERE v.plan_id=?', (plan_id,))]
            for tid in set(affected + [task_id]):
                self.store.event(db, tid, 'validation_suspended', dict(plan_id=plan_id, reason=reason, components=components))
            self.store.bump(db, task_id)
            return dict(plan_id=plan_id, reuse_suspended=True, components=components, affected_tasks=affected, revision=revision+1)
        return self.store.mutate(op_id, dict(method='validation_control', task_id=task_id, revision=revision, plan_id=plan_id, reason=reason, token=token, components=components), apply)

    @public
    def validation_check(self, task_id: str, revision: int, plan_id: str, component_id: str,
                         op_id: str, run_id: str | None = None, data_root: str = '', token: str | None = None) -> dict:
        """Execute a frozen validation component on existing output. environment/numerics/outputs require this plan's completed accounted preflight run and collected output directory. Static/analysis checks use project files. Returns a durable operation id."""
        def apply(db):
            self.store.task(db, task_id, revision, token)
            row = db.execute('SELECT * FROM validation_plans WHERE id=? AND task_id=? AND active=1', (plan_id, task_id)).fetchone()
            require(row, '本任务验证方案不存在或已停用')
            spec = json.loads(row['specification'])
            c = next((c for c in spec['components'] if c['id'] == component_id), None)
            require(c, '验证组件不存在')
            if c['scope'] in ('environment', 'numerics', 'outputs'):
                run = db.execute('SELECT r.* FROM runs r JOIN run_validations v ON v.run_id=r.id WHERE r.id=? AND r.task_id=? AND v.plan_id=?', (run_id, task_id, plan_id)).fetchone()
                require(run and run['state'] == 'completed' and run['job_id'], '该组件需要已完成的记账计算')
                coverage = match_plan(self.store, db, task_id, run['snapshot_id'], plan_id)
                item = next(x for x in coverage['components'] if x['id'] == component_id)
                require(not any(d.startswith(('file:', 'condition:', 'environment', 'new_input:', 'plan_disabled')) for d in item['differences']), '该作业不再匹配当前验证条件')
                expected = 'runs/' + run_id
                require(data_root == expected or data_root.startswith(expected + '/'), '使用对应作业已回收的实际数据目录')
                collection = db.execute("SELECT result FROM queue WHERE task_id=? AND kind='collect' AND state='completed' AND json_extract(payload,'$.run_id')=? ORDER BY created DESC LIMIT 1", (task_id, run_id)).fetchone()
                require(collection, '先完成该作业的数据回收')
                collected = json.loads(collection[0]).get('results', [])
                hashes = next((item.get('verification_sources', {}) for item in collected), {})
                for check in c['checks']:
                    name = data_root.rstrip('/') + '/' + check['path']
                    require(name in hashes and self.store.path(task_id, name).is_file() and file_hash(self.store.path(task_id, name)) == hashes[name], '验证数据与回收回执不一致: ' + name)
            checks = [{**check, 'path': data_root.rstrip('/') + '/' + check['path'] if data_root else check['path']} for check in c['checks']]
            for check in checks:
                self.store.path(task_id, check['path'])
            self.store.bump(db, task_id)
            return self.store.queue(db, task_id, 'check', dict(checks=checks, artifacts=[], plan_id=plan_id, component=component_id))
        return self.store.mutate(op_id, dict(method='validation_check', task_id=task_id, revision=revision, plan_id=plan_id,
            component_id=component_id, run_id=run_id, data_root=data_root, token=token), apply)

    @public
    def resource_bundle(self, resource_ids: list[str], name: str, usage: dict, op_id: str) -> dict:
        """Group existing resources with native usage, element mapping, short-range configuration, applicability and source license. Metadata retains file/measurement distinctions; it grants no validation by itself."""
        require(resource_ids and name.strip() and usage.get('source') and usage.get('license'), '资源组合需要成员、名称、来源与许可')
        members = [self.resource_get(rid) for rid in resource_ids]
        value = dict(name=name, kind='author_project', members=resource_ids, usage=usage,
                     provenance=dict(source=usage['source'], license=usage['license']),
                     member_versions={r['id']: digest(encode(self.store.setting(r['id'])).encode()) for r in members})
        def apply(db):
            rid = identifier('resource')
            self.store.set_setting(db, rid, value)
            return dict(id=rid, **value)
        return self.store.mutate(op_id, dict(method='resource_bundle', resource_ids=resource_ids, name=name, usage=usage), apply)

    @public
    def batch_prepare(self, task_id: str, revision: int, plan_id: str, snapshot_id: str,
                      samples: list[dict], op_id: str, token: str | None = None) -> dict:
        """Prepare independent production repeats using one validated seed-variable template. samples: [{sample_id,seed}]. Uses existing validation, creates immutable per-sample snapshots and no preflight jobs. Call batch_submit to persist dispatch intent."""
        require(1 <= len(samples) <= 1000 and all(set(s) == {'sample_id', 'seed'} and isinstance(s['sample_id'], str) and s['sample_id'].strip() and type(s['seed']) is int for s in samples), '样本需要唯一 sample_id 和整数 seed，最多1000项')
        require(len({s['sample_id'] for s in samples}) == len(samples) and len({s['seed'] for s in samples}) == len(samples), '独立样本编号和随机种子必须互异')
        def apply(db):
            task = self.store.task(db, task_id, revision, token)
            require(task['policy']['mode'] == 'research', '批量重复使用普通研究规则')
            require(task['state'] not in ('paused', 'stopping', 'stopped'), '任务已暂停或停止')
            match = match_plan(self.store, db, task_id, snapshot_id, plan_id)
            require(match['ready'], '批次存在未完成验证项', 'validation_required', encode(match['missing']))
            plan = json.loads(db.execute('SELECT specification FROM validation_plans WHERE id=?', (plan_id,)).fetchone()[0])
            require(len(plan['variations']) == 1, '独立重复需要一个已登记的种子变量')
            variable = plan['variations'][0]
            cluster = self.store.setting('cluster')
            require(cluster, '尚未配置集群')
            original = snapshot_files(self.store, db, task_id, snapshot_id)
            raw = (self.store.home / 'blobs' / original[variable['path']]['sha256']).read_text()
            bid, now = identifier('batch'), time.time()
            db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)', (bid, task_id, plan_id, 'prepared', encode(dict(samples=samples, snapshot_id=snapshot_id)), now))
            members = []
            for index, sample in enumerate(samples):
                require(variable['min'] <= sample['seed'] <= variable['max'], '随机种子超出验证允许范围')
                existing = db.execute('SELECT m.sample_id,m.seed FROM batch_members m JOIN batches b ON b.id=m.batch_id JOIN runs r ON r.id=m.run_id WHERE b.task_id=? AND r.condition_id=? AND (m.sample_id=? OR m.seed=?)', (task_id, digest(encode(task['constraints_json']).encode()), sample['sample_id'], sample['seed'])).fetchone()
                require(not existing, '该任务已有相同样本或随机种子，先读取原批次')
                pattern = r'(?m)^(\s*variable\s+' + re.escape(variable['variable']) + r'\s+(?:index|equal)\s+)[0-9]+'
                content, count = re.subn(pattern, lambda m: m[1] + str(sample['seed']), raw)
                require(count == 1, '种子变量定义不唯一')
                sha = digest(content.encode())
                atomic_write(self.store.home / 'blobs' / sha, content.encode())
                files = {k: dict(v) for k, v in original.items()}
                files[variable['path']].update(sha256=sha, bytes=len(content.encode()))
                sid, rid = identifier('snapshot'), identifier('run')
                db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?)', (sid, task_id, encode(files), digest(encode(files).encode()), revision, now))
                env = {k: cluster[k] for k in ('ssh_alias', 'remote_root', 'account', 'partition', 'environment_script')}
                env['fingerprint'] = digest(encode(env).encode())
                remote = cluster['remote_root'].rstrip('/') + '/' + task_id + '/' + rid
                db.execute("INSERT INTO runs(id,task_id,intent,snapshot_id,environment,rules,entry,state,cluster,remote_dir,condition_id,sample_id,purpose,created,updated) VALUES(?,?,?,?,?,?,?,'prepared',?,?,?,?,?,?,?)",
                    (rid, task_id, bid + ':' + sample['sample_id'], sid, encode(env), encode(task['policy']), plan['entry'], cluster['ssh_alias'], remote, digest(encode(task['constraints_json']).encode()), sample['sample_id'], 'research', now, now))
                db.execute('INSERT INTO run_validations VALUES(?,?,?,?,0)', (rid, plan_id, 'production', encode(match)))
                db.execute('INSERT INTO batch_members VALUES(?,?,?,?,?)', (bid, index, rid, sample['sample_id'], sample['seed']))
                dispatch_gate(self.store, db, dict(db.execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()))
                members.append(dict(run_id=rid, sample_id=sample['sample_id'], seed=sample['seed']))
            self.store.bump(db, task_id)
            self.store.event(db, task_id, 'batch_prepared', dict(batch_id=bid, samples=len(samples), preflight_jobs=0))
            return dict(batch_id=bid, revision=revision + 1, members=members, preflight_jobs=0)
        return self.store.mutate(op_id, dict(method='batch_prepare', task_id=task_id, revision=revision, plan_id=plan_id,
            snapshot_id=snapshot_id, samples=samples, token=token), apply)

    @public
    def batch_submit(self, batch_id: str, revision: int, op_id: str, token: str | None = None) -> dict:
        """Persist all prepared batch members atomically within current task authorization and submission count. Restart/retry retains member identities. Concurrency remains the task policy."""
        def apply(db):
            batch = db.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
            require(batch, '批次不存在')
            task = self.store.task(db, batch['task_id'], revision, token)
            require(task['state'] not in ('paused', 'stopping', 'stopped') and batch['state'] not in ('paused', 'stopped'), '任务或批次已暂停/停止')
            require(task['policy']['authorized'], '等待本批计算内容和资源授权')
            runs = [dict(r) for r in db.execute("SELECT r.* FROM runs r JOIN batch_members m ON m.run_id=r.id WHERE m.batch_id=? AND r.state='prepared'", (batch_id,))]
            used = db.execute("SELECT count(*) FROM runs WHERE task_id=? AND state NOT IN ('prepared','cancelled_before_dispatch','staging_failed')", (task['id'],)).fetchone()[0]
            limit = task['policy']['submission_limit']
            require(limit is None or used + len(runs) <= limit, '批次超出本任务提交次数')
            for run in runs:
                run['rules'] = encode(task['policy'])
                dispatch_gate(self.store, db, run)
                db.execute("UPDATE runs SET state='pending',rules=?,updated=? WHERE id=?", (run['rules'], time.time(), run['id']))
            db.execute("UPDATE batches SET state='active' WHERE id=?", (batch_id,))
            db.execute("UPDATE tasks SET state='active' WHERE id=?", (task['id'],))
            self.store.bump(db, task['id'])
            return dict(batch_id=batch_id, queued=len(runs), revision=revision + 1)
        return self.store.mutate(op_id, dict(method='batch_submit', batch_id=batch_id, revision=revision, token=token), apply)

    @public
    def batch_get(self, batch_id: str) -> dict:
        """Read durable sample identities, per-state counts, current costs and validation references."""
        with self.store.connect() as db:
            batch = db.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
            require(batch, '批次不存在')
            members = [dict(r) for r in db.execute('SELECT m.*,r.state,r.job_id,r.evidence,r.updated FROM batch_members m JOIN runs r ON r.id=m.run_id WHERE m.batch_id=? ORDER BY m.ordinal', (batch_id,))]
        counts, core_seconds = {}, 0
        for member in members:
            counts[member['state']] = counts.get(member['state'], 0) + 1
            member['evidence'] = json.loads(member['evidence'])
            core_seconds += member['evidence'].get('core_seconds', 0) or 0
        return {**dict(batch), 'specification': json.loads(batch['specification']), 'members': members,
                'counts': counts, 'accounted_core_hours': core_seconds / 3600}

    @public
    def batch_control(self, batch_id: str, revision: int, action: str, op_id: str) -> dict:
        """Pause future members, resume pending members, or stop one batch and cancel its active jobs. Other batches retain their controls."""
        require(action in ('pause', 'resume', 'stop'), 'action 为 pause/resume/stop')
        def apply(db):
            batch = db.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
            require(batch, '批次不存在')
            task = self.store.task(db, batch['task_id'])
            require(task['revision'] == revision, '任务已更新')
            require(batch['state'] != 'stopped' or action == 'stop', '已停止批次保留历史；新计算建立新批次')
            state = {'pause': 'paused', 'resume': 'active', 'stop': 'stopped'}[action]
            db.execute('UPDATE batches SET state=? WHERE id=?', (state, batch_id))
            if action == 'stop':
                for run in db.execute('SELECT r.* FROM runs r JOIN batch_members m ON m.run_id=r.id WHERE m.batch_id=?', (batch_id,)):
                    if run['state'] in ('prepared', 'pending', 'staging_failed'):
                        db.execute("UPDATE runs SET state='cancelled_before_dispatch' WHERE id=?", (run['id'],))
                    elif run['state'] in ('queued', 'running', 'dispatching', 'unknown'):
                        evidence = {**json.loads(run['evidence']), 'cancel_requested': True}
                        db.execute('UPDATE runs SET evidence=? WHERE id=?', (encode(evidence), run['id']))
                        self.store.queue(db, task['id'], 'cancel', dict(run_id=run['id']))
            self.store.bump(db, task['id'])
            self.store.event(db, task['id'], 'batch_controlled', dict(batch_id=batch_id, action=action))
            return dict(batch_id=batch_id, state=state, revision=revision + 1)
        return self.store.mutate(op_id, dict(method='batch_control', batch_id=batch_id, revision=revision, action=action), apply)
