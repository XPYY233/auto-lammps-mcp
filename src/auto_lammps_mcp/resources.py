"""Read existing catalogs and native files without downloading or rewriting them."""
from __future__ import annotations

import json
from pathlib import Path

from .core import Fault, digest, encode, require
from .files import file_hash


def contained_file(root, name):
    relative = Path(name)
    require(not relative.is_absolute() and '..' not in relative.parts, '资源文件必须位于登记目录内')
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()) and path.is_file(), '资源文件缺失或位于登记目录外')
    return path


def check_files(root, files):
    checked = []
    for row in files:
        path = contained_file(root, row['name'])
        actual = file_hash(path)
        require(not row.get('sha256') or row['sha256'] == actual, '资源文件摘要发生变化', 'resource_changed')
        require(row.get('size') is None or row['size'] == path.stat().st_size, '资源文件大小发生变化', 'resource_changed')
        checked.append({**row, 'sha256': actual, 'size': path.stat().st_size})
    return checked


def load_catalog(source, local_roots=None):
    source = Path(source).expanduser().resolve()
    require(source.exists(), '资源目录或清单不存在')
    entries, failures = [], []
    local_roots = local_roots or {}

    def add(identity, value):
        entries.append(dict(id='resource_' + digest(encode([str(source), identity]).encode())[:24], **value))

    if source.is_dir():
        records = sorted(source.glob('*/record.json'))
        if (source / 'record.json').is_file():
            records.insert(0, source / 'record.json')
        require(bool(records), '目录中未找到已有势函数的 record.json')
        require(len(records) <= 2000, '请分批导入资源目录')
        for record in records:
            try:
                raw = json.loads(record.read_text())
                meta = raw['metadata']
                files = check_files(record.parent, [dict(role=role, **row) for role, row in raw['files'].items()])
                require(files and meta.get('source') and meta.get('license'), '资源缺少文件、来源或许可记录')
                add(str(record.relative_to(source)), dict(name=meta['name'], kind='potential',
                    location=str(record.parent), files=files, availability='local_verified',
                    provenance={**meta, 'catalog_path': str(record), 'catalog_sha256': file_hash(record),
                                'original_status': raw.get('status'), 'inspection': raw.get('inspection', {})}))
            except (Fault, ValueError, KeyError, TypeError, OSError) as exc:
                failures.append(dict(entry=str(record), error=str(exc)))
        return entries, failures

    require(source.stat().st_size <= 32 * 1024 * 1024, '请分批导入资源清单')
    try:
        document = json.loads(source.read_text())
    except (ValueError, UnicodeError) as exc:
        raise Fault('invalid_catalog', '资源清单应为 JSON', '读取已有资源清单格式') from exc
    require(isinstance(document, dict), '资源清单应包含 tier_a、tier_b 或 tier_c')
    groups = {key: document[key] for key in ('tier_a', 'tier_b', 'tier_c') if key in document}
    require(groups and all(isinstance(rows, list) for rows in groups.values()), '未找到已有作者项目清单')
    catalog_hash = file_hash(source)
    require(sum(map(len, groups.values())) <= 5000, '请分批导入作者项目清单')
    for tier, rows in groups.items():
        for number, raw in enumerate(rows):
            try:
                require(isinstance(raw, dict) and raw.get('repo'), '项目缺少仓库与固定版本')
                original_source = raw.get('source') if isinstance(raw.get('source'), dict) else {}
                repo, commit = raw['repo'], raw.get('commit') or original_source.get('version')
                require(commit, '项目缺少仓库与固定版本')
                files = [dict(role=role, name=name) for role, key in [('code', 'inputs'), ('potential', 'potentials')]
                         for name in raw.get(key, [])]
                location = local_roots.get(repo)
                if location:
                    location = str(Path(location).expanduser().resolve())
                    files = check_files(Path(location), files)
                add([repo, commit, tier], dict(name=raw.get('source_label') or raw.get('title') or repo,
                    kind='potential' if raw.get('repository_role') == 'potential_library' else 'author_project',
                    location=location or raw.get('source_url') or f'https://github.com/{repo}/tree/{commit}',
                    availability='local_verified' if location and files else 'indexed', files=files,
                    provenance={**raw, 'source': {**original_source, 'repository': repo, 'revision': commit},
                                'license': raw.get('license') or '未记录', 'original_tier': tier,
                                'catalog_path': str(source), 'catalog_sha256': catalog_hash}))
            except (Fault, ValueError, KeyError, TypeError, OSError) as exc:
                failures.append(dict(entry=f'{tier}[{number}]', error=str(exc)))
    return entries, failures


def read_native(value, path, offset, limit):
    require(value.get('availability') == 'local_verified', '该条目尚未绑定已有本地文件',
            'resource_not_local', '用资源目录导入，或在导入作者清单时提供已有代码目录 local_roots')
    require(0 <= offset and 1 <= limit <= 8192, 'offset 非负，limit 为 1 至 8192 个字符')
    expected = next((f for f in value.get('files', []) if f['name'] == path), None)
    require(expected is not None, '文件未在该资源清单中登记')
    file = contained_file(Path(value['location']), path)
    require(file.stat().st_size <= 16 * 1024 * 1024, '大文件使用原生分析工具读取')
    require(file_hash(file) == expected['sha256'], '资源文件摘要发生变化', 'resource_changed', '重新核对原资源及版本')
    try:
        text = file.read_text(encoding='utf-8')
    except UnicodeError as exc:
        raise Fault('binary_resource', '该文件应由对应原生工具读取', '使用登记路径读取二进制模型') from exc
    require(offset <= len(text), 'offset 超出文件长度')
    end = min(len(text), offset + limit)
    return dict(path=path, text=text[offset:end], offset=offset, next_offset=end,
                more=end < len(text), sha256=expected['sha256'], total_characters=len(text),
                source=value['provenance'].get('source'), license=value['provenance'].get('license'))
