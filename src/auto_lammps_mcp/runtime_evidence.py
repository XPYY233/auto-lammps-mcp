"""Persist observed runtime identities, separately from scientific validation."""
from __future__ import annotations

import time
from .core import digest, encode, identifier, require
from .research_store import allowed


def selection(config, commands, files):
    return dict(environment_script=config['environment_script'],
                commands=sorted(commands), files=sorted(files))


def remember(store, task_id, selected, measured):
    value = dict(id=identifier('runtime'), task_id=task_id, selection=selected,
                 **measured, observed_at=time.time())
    with store.transaction() as db:
        store.set_setting(db, value['id'], value)
        prefix = 'runtime_compute:' if measured.get('role') == 'compute' else 'runtime_latest:'
        store.set_setting(db, prefix + digest(encode(selected).encode()), value['id'])
        store.event(db, task_id, 'runtime_observed', value)
    return value


def read(store, db, task_id, runtime_id):
    value = store.setting(runtime_id) if runtime_id else None
    require(value and runtime_id.startswith('runtime_') and allowed(db, task_id, value['task_id']),
            '先通过 environment_capture 读取引擎、启动器及外部依赖的实际文件身份', 'runtime_required')
    require(value.get('fingerprint') and value.get('identity'), '环境文件读取未完成，先处理回执中的缺项', 'runtime_unavailable')
    return value


def current(store, record, role='login'):
    key = ('runtime_compute:' if role == 'compute' else 'runtime_latest:') + digest(encode(record['selection']).encode())
    latest = store.setting(key)
    return store.setting(latest) if latest else None


def node_receipt(value, runtime, job_id):
    from .runtime_probe import fingerprint, program_identity
    identity = value.get('identity')
    require(identity and value.get('role') == 'compute' and str(value.get('job_id')) == str(job_id)
            and value.get('fingerprint') == fingerprint(identity),
            '需要该记账作业实际计算节点的完整环境回执', 'runtime_receipt')
    require(program_identity(identity) == program_identity(runtime['identity']),
            '计算节点引擎或显式依赖与准备时不同', 'runtime_changed')
    return value
