"""Versioned research memory, evidence receipts and reusable plan storage."""
from __future__ import annotations

import json
import re
import time

from .core import digest, encode, identifier, require
from .files import file_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
 revision INTEGER NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
 conditions TEXT NOT NULL, evidence TEXT NOT NULL, source_event INTEGER, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memory_versions (
 id TEXT NOT NULL, revision INTEGER NOT NULL, record TEXT NOT NULL, reason TEXT NOT NULL,
 created REAL NOT NULL, PRIMARY KEY(id,revision));
CREATE TABLE IF NOT EXISTS validation_plans (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
 specification TEXT NOT NULL, fingerprint TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS check_receipts (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL, plan_id TEXT, component TEXT,
 specification TEXT NOT NULL, result TEXT NOT NULL, sources TEXT NOT NULL,
 passed INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS run_validations (
 run_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, stage TEXT NOT NULL,
 coverage TEXT NOT NULL, reserved_core_hours REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS batches (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL, plan_id TEXT NOT NULL,
 state TEXT NOT NULL, specification TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS batch_members (
 batch_id TEXT NOT NULL, ordinal INTEGER NOT NULL, run_id TEXT NOT NULL UNIQUE,
 sample_id TEXT NOT NULL, seed INTEGER NOT NULL, PRIMARY KEY(batch_id,ordinal),
 UNIQUE(batch_id,sample_id), UNIQUE(batch_id,seed));
CREATE INDEX IF NOT EXISTS memory_task ON memories(task_id,kind,status);
CREATE INDEX IF NOT EXISTS checks_plan ON check_receipts(plan_id,component,created);
"""

ALIASES = {
 'tungsten': ('钨', 'w'), 'nickel': ('镍', 'ni'), 'cascade': ('级联', '辐照', 'pka'),
 'vacancy': ('空位',), 'diffusion': ('扩散', '输运'), 'temperature': ('温度',),
 'timestep': ('步长', '时间步'), 'potential': ('势函数', '势文件'),
 'energy': ('能量', '总能'), 'trajectory': ('轨迹', 'dump'), 'tensile': ('拉伸',),
}


def tokens(text):
    text = text.casefold()
    result = set(re.findall(r'[a-z0-9_./-]+|[\u4e00-\u9fff]', text))
    result.update(text[i:i + 2] for i in range(len(text) - 1)
                  if all('\u4e00' <= c <= '\u9fff' for c in text[i:i + 2]))
    for canonical, variants in ALIASES.items():
        if canonical in result or any(v in result or (len(v) > 1 and v in text) for v in variants):
            result.add(canonical)
    return result


def allowed(db, target_id, source_id):
    target = db.execute('SELECT policy FROM tasks WHERE id=?', (target_id,)).fetchone()
    source = db.execute('SELECT policy FROM tasks WHERE id=?', (source_id,)).fetchone()
    if not target or not source:
        return False
    a, b = json.loads(target[0])['mode'], json.loads(source[0])['mode']
    return target_id == source_id or (a == b and a in ('research', 'author_reference'))


def memory_value(row):
    value = dict(row)
    for key in ('conditions', 'evidence'):
        value[key] = json.loads(value[key])
    return value


def save_memory(db, value, reason):
    previous = db.execute('SELECT revision FROM memories WHERE id=?', (value['id'],)).fetchone()
    value = {**value, 'revision': previous[0] + 1 if previous else 1, 'updated': time.time()}
    fields = ('id', 'task_id', 'kind', 'status', 'revision', 'title', 'summary', 'conditions', 'evidence', 'source_event', 'updated')
    db.execute('INSERT OR REPLACE INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?)',
               tuple(encode(value[k]) if k in ('conditions', 'evidence') else value.get(k) for k in fields))
    db.execute('INSERT INTO memory_versions VALUES(?,?,?,?,?)',
               (value['id'], value['revision'], encode(value), reason, time.time()))
    return value


def remember_event(db, task_id, kind, data, seq):
    if not task_id or kind not in {'task_created', 'task_updated', 'decision_needed',
                                  'research_completed', 'artifact_review_failed', 'validation_checked'}:
        return
    task = db.execute('SELECT title,request,constraints_json FROM tasks WHERE id=?', (task_id,)).fetchone()
    if not task:
        return
    category = 'failure' if kind in ('decision_needed', 'artifact_review_failed') else 'task'
    # Store short factual pointers, not copied credentials or arbitrary command output.
    summary = task['request'] if kind == 'task_created' else kind + ': ' + str(data.get('reason', data.get('component', '')))
    save_memory(db, dict(id='memory_event_' + str(seq), task_id=task_id, kind=category,
                        status='observed', title=task['title'], summary=summary[:3000],
                        conditions=json.loads(task['constraints_json']), evidence={'event_seq': seq}, source_event=seq), kind)


def evidence_current(store, task_id, sources):
    return all(store.path(task_id, name).is_file() and file_hash(store.path(task_id, name)) == sha
               for name, sha in sources.items())


def search_memory(store, db, task_id, query='', limit=5, verify=True):
    require(1 <= limit <= 50, 'limit 取 1 至 50')
    wanted, matches = tokens(query), []
    for row in db.execute("SELECT * FROM memories WHERE status NOT IN ('disabled','superseded') ORDER BY updated DESC"):
        if not allowed(db, task_id, row['task_id']):
            continue
        value = memory_value(row)
        found = wanted & tokens(value['title'] + ' ' + value['summary'] + ' ' + encode(value['conditions']))
        if wanted and not found:
            continue
        value['matched_terms'] = sorted(found)
        matches.append((len(found), value['status'] == 'verified', value['updated'], value))
    matches.sort(key=lambda x: x[:3], reverse=True)
    selected = []
    for match in matches[:limit]:
        value = match[3]
        current = evidence_current(store, value['task_id'], value['evidence'].get('files', {})) if verify else None
        receipt_id = value['evidence'].get('receipt_id')
        if receipt_id and verify:
            from .verification import receipt_current
            current = current and receipt_current(store, db, value['task_id'], receipt_id)
        value.update(evidence_current=current,
                     reuse='reference' if value['status'] != 'verified' else 'check_conditions' if current is not False else 'evidence_changed')
        selected.append(value)
    return dict(items=selected, total=len(matches),
                next_action='memory_get 读取来源；validation_match 判断当前输入的覆盖，授权读取当前任务')


def stage_context(store, db, task_id):
    task = store.task(db, task_id)
    query = task['title'] + ' ' + task['request'] + ' ' + encode(task['constraints_json'])
    memory = search_memory(store, db, task_id, query, limit=3, verify=False)
    memory['items'] = [dict(id=x['id'], task_id=x['task_id'], title=x['title'][:120], summary=x['summary'][:160],
                            kind=x['kind'], status=x['status'], reuse=x['reuse'], evidence_current=x['evidence_current'],
                            plan_id=x['evidence'].get('plan_id'), read='memory_get') for x in memory['items'][:3]]
    return dict(memory=memory, confirmed_conditions=task['constraints_json'],
                questions=['研究问题与必需成果是否明确？', '哪些条件固定，哪些希望获得建议？'],
                workflow=['检索已有记忆与成套资源', '讨论科学选择', '匹配已有验证',
                          '只补缺项', '正式运行或批量重复', '分析核验并交付'],
                control=task['state'], next_action='读取相关记忆与资源；按用户目标确认必要选择')
