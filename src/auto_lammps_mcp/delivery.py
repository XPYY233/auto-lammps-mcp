"""Batch analysis inputs and versioned, readable research delivery."""
from __future__ import annotations

import json
from pathlib import Path

from .core import encode, require
from .files import file_hash
from .verification import receipt_current


def batch_dataset(store, db, task_id, batch_id, files):
    require(isinstance(files, list) and files and all(isinstance(p, str) and p for p in files), '选择每个样本要读取的相对文件')
    require(len(set(files)) == len(files), '样本文件路径重复')
    batch = db.execute('SELECT * FROM batches WHERE id=? AND task_id=?', (batch_id, task_id)).fetchone()
    require(batch, '该批次不属于当前任务')
    members, bindings, hashes = [], {}, {}
    runs = {r['id']: dict(r) for r in db.execute('SELECT id,state,parent_id,condition_id,sample_id,snapshot_id,created FROM runs WHERE task_id=? ORDER BY created', (task_id,))}
    for row in db.execute('SELECT m.*,r.state,r.condition_id,r.parent_id,r.snapshot_id FROM batch_members m JOIN runs r ON r.id=m.run_id WHERE m.batch_id=? ORDER BY m.ordinal', (batch_id,)):
        member = dict(row)
        chain = {member['run_id']}
        for run in runs.values():
            if run['parent_id'] in chain and run['condition_id'] == member['condition_id'] and run['sample_id'] == member['sample_id']:
                chain.add(run['id'])
        leaves = chain - {runs[r]['parent_id'] for r in chain}
        member['original_run_id'] = member['run_id']
        member['attempts'] = [runs[r] for r in runs if r in chain]
        if len(leaves) == 1:
            last = runs[leaves.pop()]
            member.update(run_id=last['id'], state=last['state'], snapshot_id=last['snapshot_id'])
        else:
            member['state'] = 'ambiguous_attempts'
        selected, missing = {}, []
        for name in files:
            # Validate before prefixing, including traversal and absolute paths.
            store.path(task_id, name)
            require(not Path(name).is_absolute() and '..' not in Path(name).parts and name != '.', '样本文件必须是作业目录内的相对文件')
            source = 'runs/' + member['run_id'] + '/' + name
            path = store.path(task_id, source)
            if path.is_file() and path.stat().st_size:
                selected['samples/' + member['run_id'] + '/' + name] = source
            else:
                missing.append(name)
        included = member['state'] == 'completed' and not missing
        member.update(included=included, missing_files=missing, files=selected,
                      reason='ready' if included else 'run_' + member['state'] if member['state'] != 'completed' else 'missing_files')
        if included:
            bindings.update(selected)
            hashes.update({source: file_hash(store.path(task_id, source)) for source in selected.values()})
        members.append(member)
    return dict(batch_id=batch_id, plan_id=batch['plan_id'], task_id=task_id, members=members,
                planned=len(members), included=sum(m['included'] for m in members),
                excluded=sum(not m['included'] for m in members), input_bindings=bindings,
                source_hashes=hashes, manifest_file='batch-samples.json',
                next_action='用 analysis_submit 的 batch_id/batch_files 绑定该批次；脚本读取 batch-samples.json，报告实际样本数和排除原因')


def artifact_catalog(store, db, task_id):
    latest = {r['path']: dict(r) for r in db.execute('SELECT * FROM artifacts WHERE task_id=? ORDER BY created,rowid', (task_id,))}
    metadata = {r['key'].rsplit(':', 1)[-1]: json.loads(r['value']) for r in db.execute('SELECT key,value FROM settings WHERE key LIKE ?', ('artifact_meta:' + task_id + ':%',))}
    # Include every explicit replacement edge, even when the replacement is later reviewed again.
    replaced = {old for meta in metadata.values() for old in meta.get('supersedes', [])}
    hashes = {}
    def current(name, expected):
        if name not in hashes:
            path = store.path(task_id, name)
            hashes[name] = file_hash(path) if path.is_file() else None
        return hashes[name] == expected
    items = []
    for row in latest.values():
        sources, requirements, checks = (json.loads(row[k]) for k in ('sources', 'requirements', 'checks'))
        meta = metadata.get(row['id'], {})
        intact = all(current(name, sha) for name, sha in {**sources, row['path']: row['sha256']}.items())
        measured = bool(checks) and all(c.get('receipt_id') and receipt_current(store, db, task_id, c['receipt_id'], row['path']) for c in checks)
        status = 'superseded' if row['id'] in replaced else 'stale' if row['stale'] or not intact else 'current' if measured else 'check_failed'
        items.append(dict(artifact_id=row['id'], path=row['path'], absolute_path=str(store.path(task_id, row['path'])),
                          description=meta.get('description', ''), status=status, requirements=requirements,
                          sources=list(sources), checks=checks, supersedes=meta.get('supersedes', []),
                          preview=dict(method='artifact_preview', task_id=task_id, path=row['path'])))
    # Preserve source-to-result invalidation in a read-only overview as well.
    changed = True
    while changed:
        changed = False
        stale = {x['path'] for x in items if x['status'] == 'stale'}
        for item in items:
            if item['status'] in ('current', 'check_failed') and stale.intersection(item['sources']):
                item['status'], changed = 'stale', True
    satisfied = {r for x in items if x['status'] == 'current' for r in x['requirements']}
    failed = {r for x in items if x['status'] == 'check_failed' for r in x['requirements']}
    return items, satisfied, failed


def results_view(store, db, task_id):
    task = store.task(db, task_id)
    items, satisfied, failed = artifact_catalog(store, db, task_id)
    requirements = [{**r, 'status': 'archive_check_required' if r.get('kind') == 'archive' else 'check_failed' if r['id'] in failed else 'ready' if r['id'] in satisfied else 'missing',
                     'artifacts': [x['artifact_id'] for x in items if r['id'] in x['requirements'] and x['status'] != 'superseded']}
                    for r in task['requirements']]
    analyses = []
    for row in db.execute("SELECT id,state,payload,result FROM queue WHERE task_id=? AND kind='analysis' ORDER BY created DESC LIMIT 10", (task_id,)):
        payload = json.loads(row['payload'])
        result = json.loads(row['result']) if row['result'] else {}
        evidence = result.get('error', {}).get('evidence', result)
        analyses.append(dict(operation_id=row['id'], state=row['state'], method=payload.get('method', {}),
                             supersedes=payload.get('supersedes'), batch_id=payload.get('batch', {}).get('batch_id'),
                             receipt_path=evidence.get('receipt_path'), log_path=evidence.get('log_path'),
                             read=dict(method='operation_get', operation_id=row['id'])))
    origins = []
    for row in db.execute("SELECT result FROM queue WHERE task_id=? AND kind='import' AND state='completed'", (task_id,)):
        imported = json.loads(row[0])
        if imported.get('original_record'):
            origins.append({k: imported[k] for k in ('original_task', 'original_request', 'original_requirements', 'original_record') if k in imported})
    archives = []
    for row in db.execute('SELECT value FROM settings WHERE key LIKE ?', ('delivery:' + task_id + ':%',)):
        entry = json.loads(row[0])
        archives.append(dict(filename=entry['filename'], path=str(store.home / 'exports' / entry['filename']),
                             requirements=entry['requirement_ids'], verification='completion_check'))
    return dict(task_id=task_id, request=task['request'], requirements=requirements, artifacts=items,
                analyses=analyses, imported_projects=origins, archives=archives,
                missing=[r['id'] for r in requirements if r['status'] not in ('ready', 'archive_check_required')],
                next_action='补齐缺项，展示当前成果；导出后 completion_check 核对全部交付，修改分析保留原版本')


def handoff_readme(task, catalog):
    lines = ['Auto-LAMMPS 研究交接', '', task['title'], task['request'], '', '成果与要求']
    lines += [f"- {r['description']}：{r['status']}" for r in catalog['requirements']]
    lines += ['', '结果入口（相对本解压目录）']
    lines += [f"- {a['description'] or a['path']} [{a['status']}]：project/{a['path']}" for a in catalog['artifacts']]
    lines += ['', '最近分析与方法']
    lines += [f"- {a['operation_id']} [{a['state']}]：{encode(a['method'])}；回执 project/{a['receipt_path']}" for a in catalog['analyses']]
    lines += ['', '继续研究', '在 Codex 或 DSH 中说：使用 Auto-LAMMPS 导入这个研究结果包，读取成果目录和剩余要求，然后继续分析。',
              'research-record.json 保存原始需求、执行、分析版本、成果与来源；events.json 保存完整历史。',
              'project/ 保存原生文件；project-manifest.json 保存逐文件 SHA-256。']
    return '\n'.join(lines) + '\n'
