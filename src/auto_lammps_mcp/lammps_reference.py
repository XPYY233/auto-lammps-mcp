"""Versioned official references and evidence-based native input diagnostics."""
from __future__ import annotations

import json
import re
import shlex
import time
import urllib.request
from pathlib import Path

from .core import Fault, digest, encode, require
from .files import atomic_write

ALIASES = {'compute pe/atom': 'compute_pe_atom', 'fix nvt': 'fix_nh',
           'fix npt': 'fix_nh', 'fix nph': 'fix_nh', 'fix nve': 'fix_nve',
           'pair_style eam/alloy': 'pair_eam', 'pair_style eam/fs': 'pair_eam',
           'pair_style eam': 'pair_eam', 'variable accuracy': 'variable',
           'input parsing': 'Commands_parse'}


def reference_identity(command, engine_version):
    page = ALIASES.get(command, command.replace(' ', '_').replace('/', '_'))
    require(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', page), '命令名称应对应官方文档名称')
    ref = engine_version
    if re.fullmatch(r'\d{1,2}[A-Z][a-z]{2}\d{4}', ref):
        ref = 'patch_' + ref
    require(re.fullmatch(r'(?:patch|stable)_\d{1,2}[A-Z][a-z]{2}\d{4}(?:_update\d+)?|[0-9a-f]{40}', ref),
            '提供引擎对应的固定版本，例如 28Mar2023、stable_2Aug2023 或完整提交号')
    return page, ref, f'https://raw.githubusercontent.com/lammps/lammps/{ref}/doc/src/{page}.rst'


def fetch_official(url):
    from .network import background_environment
    env = background_environment()
    proxies = {k.lower(): env[k + '_PROXY'] for k in ('HTTP', 'HTTPS') if env.get(k + '_PROXY')}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    with opener.open(urllib.request.Request(url, headers={'User-Agent': 'auto-lammps-mcp'}), timeout=20) as response:
        require(response.url == url, '官方文档地址发生跳转，请核对版本')
        data = response.read(2 * 1024 * 1024 + 1)
    require(len(data) <= 2 * 1024 * 1024, '文档超过单文件读取范围')
    return data.decode('utf-8')


def read_reference(home, command, engine_version, offset=0, limit=4096):
    require(offset >= 0 and 1 <= limit <= 4096, 'offset 非负，limit 为 1 至 4096')
    page, ref, url = reference_identity(command, engine_version)
    path = Path(home) / 'references' / 'lammps' / ref / (page + '.json')
    cached = path.exists()
    if cached:
        try:
            record = json.loads(path.read_text())
            require(record['source_url'] == url and record['sha256'] == digest(record['text'].encode()),
                    '官方文档缓存摘要不一致', 'reference_corrupt', '保留故障缓存，核对来源后恢复该版本文档')
        except (KeyError, ValueError) as exc:
            raise Fault('reference_corrupt', '官方文档缓存无法读取', '核对缓存与官方版本') from exc
    else:
        try:
            text = fetch_official(url)
        except (OSError, UnicodeError) as exc:
            raise Fault('reference_unavailable', '未取得指定版本的官方命令页',
                        '检查引擎版本和命令页名称，恢复连接后使用同一请求', evidence={'source_url': url}) from exc
        record = dict(source_url=url, engine_ref=ref, text=text,
                      sha256=digest(text.encode()), fetched_at=time.time())
        atomic_write(path, encode(record).encode())
    text = record['text']
    require(offset <= len(text), 'offset 超出文档')
    end = min(len(text), offset + limit)
    return {**record, 'text': text[offset:end], 'command': command, 'cached': cached,
            'offset': offset, 'next_offset': end, 'more': end < len(text), 'total_characters': len(text),
            'next': {'command': command, 'engine_version': ref, 'offset': end, 'limit': limit} if end < len(text) else None}


def logical_lines(text):
    pending, start = '', 1
    for number, line in enumerate(text.splitlines(), 1):
        if not pending:
            start = number
        # Preserve quoted comments for shlex; multiline native control is reported separately.
        if line.rstrip().endswith('&'):
            pending += line.rstrip()[:-1] + ' '
            continue
        yield start, pending + line
        pending = ''
    if pending:
        yield start, pending


def review_text(text, engine_version=''):
    commands, findings, topics = [], [], set()
    dynamic = bool(re.search(r'^\s*(include|jump|if|python|shell|next|label)\b|\$\{', text, re.M))
    for number, line in logical_lines(text):
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            dynamic = True
            continue
        if words:
            commands.append((number, words))
    computes, consumers, current = {}, {}, set()
    ran = False

    def closure(ids):
        result = set(ids)
        for _ in range(len(computes) + 1):
            added = set().union(*(computes.get(i, set()) for i in result)) if result else set()
            if added <= result:
                break
            result |= added
        return result

    for line, words in commands:
        command = words[0]
        refs = set(re.findall(r'\bc_([A-Za-z0-9_]+)', ' '.join(words)))
        topic = command
        if command in ('compute', 'fix') and len(words) >= 4:
            topic += ' ' + words[3]
        topics.add(topic)
        if command == 'compute' and len(words) >= 4:
            computes[words[1]] = refs
            current.discard(words[1])
        elif command == 'uncompute' and len(words) > 1:
            computes.pop(words[1], None)
            current.discard(words[1])
        elif command == 'thermo_style':
            consumers['thermo'] = refs
        elif command in ('dump', 'fix') and len(words) > 1:
            consumers[command + ':' + words[1]] = refs
        elif command in ('undump', 'unfix') and len(words) > 1:
            consumers.pop(command[2:] + ':' + words[1], None)
        elif command in ('run', 'minimize'):
            ran = True
            current = closure(set().union(*consumers.values()) if consumers else set())
        elif command in ('clear', 'read_data', 'read_restart', 'set', 'change_box', 'displace_atoms', 'delete_atoms', 'reset_timestep'):
            current.clear()
            ran = False
            if command == 'clear':
                computes.clear()
                consumers.clear()
        elif command == 'write_dump' and refs:
            missing = refs - current
            if not ran or missing:
                findings.append(dict(line=line, code='compute_not_current',
                    severity='review' if dynamic else 'error', computes=sorted(missing or refs),
                    message='write_dump 引用的 compute 没有已识别的当前步求值来源；定义 compute 或单独添加 run 0 不会主动调用它。',
                    action='在运行中由 dump 或 reduce/thermo 等实际使用该 compute，并核对输出步；也可直接使用运行中保存的轨迹帧。',
                    commands=['write_dump', 'compute pe/atom', 'variable']))
            else:
                findings.append(dict(line=line, code='verify_output_timestep', severity='review',
                    message='核对消费该 compute 的输出频率覆盖 write_dump 所在步，且其间没有修改系统状态。',
                    commands=['write_dump', 'dump', 'thermo_style']))
    references = []
    for command in sorted(topics):
        page = ALIASES.get(command, command.replace(' ', '_').replace('/', '_'))
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', page):
            references.append(dict(command=command, engine_version=engine_version or None,
                                   tool='lammps_reference', source_url=reference_identity(command, engine_version)[2]
                                   if engine_version else f'https://docs.lammps.org/{page}.html'))
    return dict(input_sha256=digest(text.encode()), engine_version=engine_version or None,
                findings=findings, errors=sum(f['severity'] == 'error' for f in findings),
                control_flow='requires_reading_dependencies' if dynamic else 'linear',
                references=references, next_action='读取相关版本的官方 Syntax、Examples、Restrictions，随后在记账 HPC 核对实际运行输出')


def review_snapshot(store, snapshot, entry):
    """Review only literal -in/-i files called by the saved entry, without execution."""
    blob = store.home / 'blobs' / snapshot[entry]['sha256']
    require(digest(blob.read_bytes()) == snapshot[entry]['sha256'], '入口快照摘要不一致')
    script = blob.read_text(errors='replace')
    paths = re.findall(r'(?:^|\s)-(?:in|i)\s+(?:"([^"$]+)"|\'([^\'$]+)\'|([^\s;$]+))', script)
    reports = []
    for group in paths:
        path = next(x for x in group if x)
        if path not in snapshot or path in [r['path'] for r in reports]:
            continue
        raw = (store.home / 'blobs' / snapshot[path]['sha256']).read_bytes()
        require(digest(raw) == snapshot[path]['sha256'], '输入快照摘要不一致')
        if len(raw) <= 2 * 1024 * 1024:
            reports.append(dict(path=path, **review_text(raw.decode('utf-8'))))
    return dict(files=reports, status='reviewed' if reports else 'native_entry_requires_agent_review',
                errors=sum(r['errors'] for r in reports))
