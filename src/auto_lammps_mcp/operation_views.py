"""Bound model-facing work records while retaining lossless detail reads."""
import json

from .core import require


def size(value):
    return len(json.dumps(value, ensure_ascii=False, indent=2))


def kind(value):
    return "object" if isinstance(value, dict) else "array" if isinstance(value, list) else "text" if isinstance(value, str) else "value"


def reference(operation_id, path, value):
    return dict(truncated=True, type=kind(value), count=len(value) if isinstance(value, (dict, list, str)) else 1,
                read=dict(method="operation_read", operation_id=operation_id, path=path))


def operation_summary(record):
    if size(record) <= 5500:
        return record
    operation_id = record["id"]
    result = {key: record[key] for key in ("id", "task_id", "kind", "state", "created", "updated") if key in record}
    result.update(truncated=True, details=reference(operation_id, [], record))
    for section in ("payload", "result"):
        value = record.get(section)
        if size(value) <= 1800:
            result[section] = value
        elif isinstance(value, dict):
            selected = {}
            # Identifiers and states precede large manifests and arrays.
            keys = sorted(value, key=lambda key: (isinstance(value[key], (dict, list)), key))
            for key in keys:
                child = value[key] if size(value[key]) <= 850 else reference(operation_id, [section, key], value[key])
                if size({**selected, key: child}) <= 1800:
                    selected[key] = child
            result[section] = selected
        else:
            result[section] = reference(operation_id, [section], value)
    return result


def operation_page(record, path, offset, limit, text_limit):
    require(isinstance(path, list) and all(isinstance(part, str) for part in path), "path 使用字段名称或数组下标字符串列表")
    require(0 <= offset and 1 <= limit <= 50 and 1 <= text_limit <= 4096, "分页范围无效")
    value = record
    for part in path:
        if isinstance(value, dict):
            require(part in value, "该记录路径不存在", "not_found", "读取上一级记录页面")
            value = value[part]
        elif isinstance(value, list):
            require(part.isdecimal() and int(part) < len(value), "数组下标无效")
            value = value[int(part)]
        else:
            require(False, "该记录路径没有下一级", "not_found", "读取上一级记录页面")
    result = dict(operation_id=record["id"], path=path, type=kind(value), offset=offset)
    if isinstance(value, (dict, list, str)):
        require(offset <= len(value), "offset 超出记录范围")
    if isinstance(value, str):
        end = min(len(value), offset + text_limit)
        while size({**result, "text": value[offset:end]}) > 5000 and end > offset:
            end = offset + (end - offset) // 2
        return dict(result, text=value[offset:end], total=len(value), next_offset=end, more=end < len(value))
    if not isinstance(value, (dict, list)):
        require(offset == 0, "单个值从 offset=0 读取")
        return dict(result, value=value, total=1, next_offset=1, more=False)
    keys = sorted(value) if isinstance(value, dict) else list(range(len(value)))
    items = []
    for key in keys[offset:offset + limit]:
        child = value[key]
        entry = dict(key=str(key), path=path + [str(key)], type=kind(child))
        if size(child) <= 1000:
            entry["value"] = child
        else:
            entry["details"] = reference(record["id"], entry["path"], child)
        if items and size(dict(result, items=items + [entry])) > 5000:
            break
        items.append(entry)
    end = offset + len(items)
    return dict(result, items=items, total=len(keys), next_offset=end, more=end < len(keys))
