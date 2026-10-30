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
    def environment_capture(self, task_id: str, revision: int, commands: list[str], op_id: str,
                            files: list[str] | None = None, token: str | None = None) -> dict:
        """Read remote installed engine/launcher bytes and resolved shared libraries; no engine invocation or physics. commands lists actual executables used by the native entry, e.g. ['lmp','mpirun'] or absolute paths. files lists extra absolute plugin/container/runtime dependencies. Returns an operation; use its runtime id in validation_plan. Repeating this capture measures changes without a new preflight job. Failures retain phase/exit_code in error.evidence; diagnose profile/module/file-reading failure from that receipt. Engine startup checks use accounted HPC."""
        from pathlib import PurePosixPath
        files = files or []
        require(commands and len(commands) <= 16 and len(files) <= 64, '列出实际引擎和启动器，以及必要的外部依赖')
        require(all(isinstance(x, str) and x and not any(c.isspace() for c in x) for x in commands), 'commands 使用程序名称或绝对路径，不含启动参数')
        require(all(isinstance(x, str) and PurePosixPath(x).is_absolute() for x in files), '外部依赖使用远端绝对路径')
        config = self.store.setting('cluster')
        require(config, '先配置集群环境')
        def apply(db):
            self.store.task(db, task_id, revision, token)
            self.store.bump(db, task_id)
            return self.store.queue(db, task_id, 'environment_capture', dict(config=config, commands=commands, files=files))
        return self.store.mutate(op_id, dict(method='environment_capture', task_id=task_id, revision=revision,
                                           commands=commands, files=files, token=token), apply)

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
        """Queue checks of existing data; no physics. Each has kind,path and optional unit,basis,role (required/advisory/candidate). Candidate/advisory results never revoke required evidence. file: min_bytes,contains; csv: columns,min_rows,bounds; json: values,bounds; thermo: phase={fix_id,style,group,marker,timestep}, columns,min_rows,energy_column,max_abs_drift,time_column. Use log_phases facts; block is a legacy selector. Raw energy drift during heat/work exchange needs a task-specific basis and balance=task_specific or an accounted energy column. trajectory: columns,min_frames,atoms; energy_jump: before_block,after_block,column,expected,tolerance. artifacts binds companion files. Read operation_get/check_get."""
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
    def log_phases(self, task_id: str, path: str) -> dict:
        """Inspect actual executed LAMMPS phases in an existing log, including fixes, groups, time step, units and normalization. Use those facts as thermo phase selectors; repeated runs/headers need no hand-counted block index. Reads existing data only."""
        from .phases import blocks
        target = self.store.path(task_id, path)
        result = []
        for index, block in enumerate(blocks(target)):
            times = [r[block['columns'].index('Time')] for r in block['rows']] if 'Time' in block['columns'] else []
            result.append(dict(block=index, phase=block['phase'], first_line=block['first_line'],
                columns=block['columns'], rows=len(block['rows']), errors=block['errors'], time_range=[times[0], times[-1]] if times else None))
        return dict(path=path, sha256=file_hash(target), phases=result,
                    guidance='选择实际 fix_id/style/group 或已输出的 marker。缺少命令回显时，在后续原生脚本保留 echo log/阶段标记；无需重跑已有数据分析。')

    @public
    def validation_plan(self, task_id: str, revision: int, snapshot_id: str, entry: str,
                        components: list[dict], op_id: str, variations: list[dict] | None = None,
                        budget_core_hours: float | None = None, reuse_from: str | None = None, token: str | None = None,
                        monitor: list[dict] | None = None, runtime_id: str | None = None,
                        reuse_sources: list[str] | None = None, rationale: str = '') -> dict:
        """Freeze task-specific criteria and native input dependencies. Components: id,scope (static/environment/numerics/outputs/analysis),files,condition_keys,checks; optional required=false for exploratory components, checker_id for a qualified native Python checker. Cover all inputs and conditions. Checks use role=required/advisory/candidate; only required evidence gates dependent work. Node monitor uses actual thermo phase selectors and action=warn (default) or explicit stop; no global physical tolerance. Changes retain rationale and parents. reuse_from or reuse_sources merges identical, valid components; changing a monitor preserves unrelated checks. Compare variants with validation_compare before selecting final criteria. variations: path,variable,min,max for seeds. Confirm policy.preflight_core_hours and preflight_budget_authorization; budget_core_hours defaults to that cap. runtime_id comes from environment_capture. Production requires static/environment/numerics/outputs; only missing checks need accounted preflight."""
        require(budget_core_hours is None or (math.isfinite(budget_core_hours) and budget_core_hours > 0), '预验证预算应为正数')
        require(components and len({c.get('id') for c in components}) == len(components), '验证组件需要唯一 id')
        variations = variations or []
        parents = list(dict.fromkeys(([reuse_from] if reuse_from else []) + (reuse_sources or [])))
        if monitor:
            validate_checks(monitor)
            require(all(c['kind'] == 'thermo' for c in monitor), '实时节点监视使用 thermo；原生专属检查通过 checker_register/checker_check 接入')
        require(len({(v.get('path'), v.get('variable')) for v in variations}) == len(variations), '种子定义重复')
        for v in variations:
            require(set(v) == {'path', 'variable', 'min', 'max'} and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', v['variable']), '种子定义需要 path/variable/min/max')
            require(type(v['min']) is int and type(v['max']) is int and 0 < v['min'] <= v['max'] <= 2147483647, '种子范围无效')
        def apply(db):
            task = self.store.task(db, task_id, revision, token)
            files = snapshot_files(self.store, db, task_id, snapshot_id)
            from .runtime_evidence import read as read_runtime
            selected_runtime = runtime_id
            if selected_runtime is None and parents:
                source = db.execute('SELECT specification FROM validation_plans WHERE id=?', (parents[0],)).fetchone()
                if source:
                    selected_runtime = json.loads(source[0]).get('runtime', {}).get('id')
            runtime = read_runtime(self.store, db, task_id, selected_runtime)
            require(runtime['selection']['environment_script'] == environment(self.store)['environment_script'],
                    '环境命令已变化，重新读取实际引擎身份')
            from .validation import confirmed_budget
            limit = confirmed_budget(task)
            budget = limit if budget_core_hours is None else budget_core_hours
            require(budget <= limit, '超出当前确认的预验证预算；更换方案不会扩大预算', 'preflight_budget')
            require(entry in files, '研究入口不在快照中')
            normalized, parameters = normalized_files(self.store, files, variations)
            inputs = set()
            for c in components:
                fields = {'id', 'scope', 'files', 'condition_keys', 'checks'}
                require(fields <= set(c) <= fields | {'required', 'checker_id'} and c['id'] and c['scope'] in SCOPES, '验证组件字段无效')
                require(type(c.get('required', True)) is bool, 'required 使用 true/false；可选检查不阻断其他工作')
                require(c['files'] and set(c['files']) <= set(files), '组件需要真实快照输入')
                require(set(c['condition_keys']) <= set(task['constraints_json']), '条件字段不在当前任务中')
                validate_checks(c['checks'])
                require(not c.get('required', True) or any(x.get('role', 'required') == 'required' for x in c['checks']),
                        '候选检查放入 required=false 组件；正式组件至少选择一项 required 判据')
                if c.get('checker_id'):
                    registered = self.store.setting(c['checker_id'])
                    require(registered and allowed(db, task_id, registered['task_id']), '原生检查程序未登记或不可访问')
                    require(any(files[p]['sha256'] == registered['script_sha256'] for p in c['files']), '组件依赖应包含已验证的原生检查程序')
                    require(len(c['checks']) == 1 and {k: v for k, v in c['checks'][0].items() if k not in ('path', 'role', 'action')} == registered['criteria'], '组件判据与已验证原生检查程序不一致')
                for check in c['checks']:
                    self.store.path(task_id, check['path'])
                inputs.update(c['files'])
            require(entry in inputs and all(v['path'] in inputs for v in variations), '入口和种子文件必须进入验证依赖')
            require(inputs == set(files), '验证快照只包含研究输入，并将全部文件列入组件依赖；使用 project_snapshot(paths=...) 排除结果文件')
            require(set().union(*(set(c['condition_keys']) for c in components)) == set(task['constraints_json']), '每项科学条件都需要关联至少一个验证组件')
            spec = dict(entry=entry, conditions=task['constraints_json'], components=components, variations=variations,
                        inputs={p: normalized[p] for p in sorted(inputs)}, snapshot_paths=sorted(files),
                        environment=environment(self.store), runtime=runtime, budget_core_hours=budget, monitor=monitor or [],
                        parents=parents, rationale=rationale)
            pid = identifier('validation')
            db.execute('INSERT INTO validation_plans VALUES(?,?,?,?,?,1,?)',
                       (pid, task_id, snapshot_id, encode(spec), digest(encode(spec).encode()), time.time()))
            reused = []
            for parent in parents:
                match = match_plan(self.store, db, task_id, snapshot_id, parent, declared_components=components)
                source = json.loads(db.execute('SELECT specification FROM validation_plans WHERE id=?', (parent,)).fetchone()[0])
                previous = {c['id']: c for c in source['components']}
                for c in components:
                    old = next((x for x in match['components'] if x['id'] == c['id'] and x['ready']), None)
                    if c['id'] not in reused and old and previous[c['id']] == c and source['variations'] == variations:
                        row = db.execute('SELECT * FROM check_receipts WHERE id=?', (old['receipt_id'],)).fetchone()
                        rid = identifier('check')
                        value = {**json.loads(row['result']), 'id': rid, 'plan_id': pid, 'reused_from': row['id']}
                        db.execute('INSERT INTO check_receipts VALUES(?,?,?,?,?,?,?,?,?)',
                                   (rid, row['task_id'], pid, c['id'], row['specification'], encode(value), row['sources'], row['passed'], time.time()))
                        reused.append(c['id'])
            self.store.bump(db, task_id)
            self.store.event(db, task_id, 'validation_planned', dict(plan_id=pid, reused=reused))
            return dict(plan_id=pid, revision=revision + 1, reused=reused, specification=spec,
                        guidance='候选判据用 role=candidate；提醒用 advisory；仅依赖 required 的工作受失败影响。修改判据说明 rationale，并复用未变组件。')
        return self.store.mutate(op_id, dict(method='validation_plan', task_id=task_id, revision=revision, snapshot_id=snapshot_id,
            entry=entry, components=components, variations=variations, budget_core_hours=budget_core_hours, reuse_from=reuse_from, token=token, monitor=monitor, runtime_id=runtime_id, reuse_sources=reuse_sources, rationale=rationale), apply)

    @public
    def validation_compare(self, task_id: str, plan_ids: list[str], snapshot_id: str) -> dict:
        """Compare candidate/final plans against the same inputs and actual receipts without new physics. Host agents may propose/check candidates concurrently, then select component criteria with a rationale in validation_plan(reuse_sources=[...]). Agreement is not measurement; preserve contrary evidence."""
        require(1 <= len(plan_ids) <= 10, '一次比较 1 至 10 份方案')
        result = []
        with self.store.connect() as db:
            for pid in plan_ids:
                match = match_plan(self.store, db, task_id, snapshot_id, pid)
                plan = json.loads(db.execute('SELECT specification FROM validation_plans WHERE id=?', (pid,)).fetchone()[0])
                receipts = [json.loads(r[0]) for r in db.execute('SELECT result FROM check_receipts WHERE plan_id=? ORDER BY created', (pid,))]
                result.append(dict(plan_id=pid, components=plan['components'], rationale=plan.get('rationale', ''), match=match, receipts=receipts))
        return dict(plans=result, physics_submissions=0, next_action='依据方法适用性和实测证据选择/合并组件；保留候选分歧，不以通过票数选择科学判据')

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
                         op_id: str, run_id: str | None = None, data_root: str = '', token: str | None = None,
                         analysis_operation_id: str | None = None) -> dict:
        """Execute one frozen component on existing evidence. environment/numerics/outputs use run_id and collected data_root='runs/<run_id>'; a terminal failed job can supply complete usable components. Matching old runs can be rechecked under revised criteria without another simulation. A component with checker_id additionally uses analysis_operation_id from analysis_submit on that run's collected data; program version, qualified fixtures and raw data are verified. Static/analysis checks use project files. Returns a durable operation id; missing evidence affects this component only."""
        def apply(db):
            self.store.task(db, task_id, revision, token)
            row = db.execute('SELECT * FROM validation_plans WHERE id=? AND task_id=? AND active=1', (plan_id, task_id)).fetchone()
            require(row, '本任务验证方案不存在或已停用')
            spec = json.loads(row['specification'])
            c = next((c for c in spec['components'] if c['id'] == component_id), None)
            require(c, '验证组件不存在')
            if c['scope'] in ('environment', 'numerics', 'outputs'):
                run = db.execute('SELECT r.*,p.specification AS source_plan FROM runs r JOIN run_validations v ON v.run_id=r.id JOIN validation_plans p ON p.id=v.plan_id WHERE r.id=? AND r.task_id=?', (run_id, task_id)).fetchone()
                require(run and run['state'] in ('completed', 'failed', 'cancelled', 'timeout') and run['job_id'], '该组件需要已结束并记账的计算；按实际完整数据检查对应部分')
                source_conditions = json.loads(run['source_plan'])['conditions']
                require(all(source_conditions.get(k) == spec['conditions'].get(k) for k in c['condition_keys']), '原作业条件与当前检查不匹配')
                coverage = match_plan(self.store, db, task_id, run['snapshot_id'], plan_id, conditions=source_conditions)
                item = next(x for x in coverage['components'] if x['id'] == component_id)
                checker_files = set()
                if c.get('checker_id'):
                    registered = self.store.setting(c['checker_id'])
                    checker_files = {p for p in c['files'] if spec['inputs'][p]['sha256'] == registered['script_sha256']}
                require(not any(d.startswith(('file:', 'condition:', 'environment', 'runtime', 'new_input:', 'new_condition:', 'plan_disabled'))
                                and d not in {'file:' + p for p in checker_files} for d in item['differences']), '该作业不再匹配当前验证条件')
                require(json.loads(run['environment'])['environment_script'] == spec['environment']['environment_script'], '该作业实际启动环境与验证方案不一致')
                expected = 'runs/' + run_id
                require(data_root == expected or data_root.startswith(expected + '/'), '使用对应作业已回收的实际数据目录')
                collection = db.execute("SELECT result FROM queue WHERE task_id=? AND kind='collect' AND state='completed' AND json_extract(payload,'$.run_id')=? ORDER BY created DESC LIMIT 1", (task_id, run_id)).fetchone()
                require(collection, '先完成该作业的数据回收')
                collected = json.loads(collection[0]).get('results', [])
                hashes = next((item.get('verification_sources', {}) for item in collected), {})
                runtime_path = 'runs/' + run_id + '/.alm-runtime.json'
                require(runtime_path in hashes and self.store.path(task_id, runtime_path).is_file()
                        and file_hash(self.store.path(task_id, runtime_path)) == hashes[runtime_path],
                        '计算节点环境身份缺失或与回收回执不一致')
                for check in ([] if c.get('checker_id') else c['checks']):
                    name = data_root.rstrip('/') + '/' + check['path']
                    require(name in hashes and self.store.path(task_id, name).is_file() and file_hash(self.store.path(task_id, name)) == hashes[name], '验证数据与回收回执不一致: ' + name)
                from .runtime_evidence import node_receipt
                measured_runtime = node_receipt(json.loads(self.store.path(task_id, runtime_path).read_text()), spec['runtime'], run['job_id'])
                require(measured_runtime.get('passed'), '计算节点环境检查未通过')
            checks = [{**check, 'path': data_root.rstrip('/') + '/' + check['path'] if data_root else check['path']} for check in c['checks']]
            artifacts = []
            if c.get('checker_id'):
                from .checkers import analysis_evidence
                from .research_store import evidence_current
                checker = self.store.setting(c['checker_id'])
                require(checker and evidence_current(self.store, checker['task_id'], checker['sources']), '检查程序样例证据已变化')
                item = analysis_evidence(self.store, db, task_id, analysis_operation_id, c['checks'][0]['path'])
                require(item['script_sha256'] == checker['script_sha256'], '分析程序与已验证程序不一致')
                if c['scope'] in ('environment', 'numerics', 'outputs'):
                    raw = {p: sha for p, sha in item['sources'].items() if p.startswith(expected + '/')}
                    require(raw and all(hashes.get(p) == sha for p, sha in raw.items()), '原生检查必须读取对应作业已回收的真实数据')
                checks = [{**c['checks'][0], 'path': item['path']}]
                artifacts = list(item['sources'])
            if c['scope'] in ('environment', 'numerics', 'outputs'):
                checks.append(dict(kind='json', path=runtime_path,
                                   values={'passed': True, 'fingerprint': measured_runtime['fingerprint'],
                                           'identity': measured_runtime['identity'], 'role': 'compute', 'job_id': measured_runtime['job_id']}))
            for check in checks:
                self.store.path(task_id, check['path'])
            self.store.bump(db, task_id)
            return self.store.queue(db, task_id, 'check', dict(checks=checks, artifacts=artifacts, plan_id=plan_id, component=component_id,
                compute_runtime=measured_runtime if c['scope'] in ('environment', 'numerics', 'outputs') else None))
        return self.store.mutate(op_id, dict(method='validation_check', task_id=task_id, revision=revision, plan_id=plan_id,
            component_id=component_id, run_id=run_id, data_root=data_root, token=token, analysis_operation_id=analysis_operation_id), apply)

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
            self.store.event(db, task['id'], 'batch_submitted', dict(batch_id=batch_id, queued=len(runs)))
            return dict(batch_id=batch_id, queued=len(runs), revision=revision + 1)
        return self.store.mutate(op_id, dict(method='batch_submit', batch_id=batch_id, revision=revision, token=token), apply)

    @public
    def batch_list(self, task_id: str, offset: int = 0, limit: int = 20) -> dict:
        """Find durable batches in a resumed project. Return compact counts, controls, costs and batch_get links; follow next_offset for more."""
        from .batch_views import batch_view
        require(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 50, 'offset >= 0，limit 为 1–50')
        with self.store.connect() as db:
            self.store.task(db, task_id)
            total = db.execute('SELECT count(*) FROM batches WHERE task_id=?', (task_id,)).fetchone()[0]
            rows = db.execute('SELECT * FROM batches WHERE task_id=? ORDER BY created DESC,id LIMIT ? OFFSET ?', (task_id, limit, offset)).fetchall()
            items = [batch_view(db, row, members=False) for row in rows]
        more = offset + len(items) < total
        return dict(task_id=task_id, items=items, total=total, offset=offset, more=more, next_offset=offset + len(items) if more else None)

    @public
    def batch_get(self, batch_id: str, include_details: bool = False) -> dict:
        """Read batch progress, sample identities, controls, costs and latest output steps/time. Missing accounting stays unknown. Computational completion leads to output analysis/checks. Read run_get for full evidence or include_details for all member evidence."""
        from .batch_views import batch_view
        with self.store.connect() as db:
            batch = db.execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
            require(batch, '批次不存在')
            return batch_view(db, batch, details=include_details)

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
