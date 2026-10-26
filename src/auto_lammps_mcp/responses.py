"""Persistent, lossless host replies within ordinary conversation output windows."""
import json
import re

from .core import digest, encode, require
from .files import atomic_write
from .operation_views import operation_page, reference, size


def bound_response(store, method, value):
    if size(value) <= 5500:
        return value
    content = encode(value).encode()
    response_id = digest(content)
    path = store.home / "responses" / (response_id + ".json")
    if not path.exists():
        atomic_write(path, content)

    def link(parts, item):
        return reference(response_id, parts, item, method="response_read", id_key="response_id")

    def compact(item, parts, depth, budget):
        if not isinstance(item, dict) or depth > 1:
            return link(parts, item)
        result = dict(truncated=True, details=link(parts, item))
        keys = sorted(item, key=lambda key: (isinstance(item[key], (dict, list)), key))
        for key in keys:
            if key in result:
                continue
            child = item[key] if size(item[key]) <= 800 else compact(item[key], parts + [key], depth + 1, 1300)
            if size({**result, key: child}) <= budget:
                result[key] = child
        return result

    result = compact(value, [], 0, 4700)
    return {**result, "response_id": response_id, "response_method": method}


def read_response(store, response_id, path, offset, limit, text_limit):
    require(isinstance(response_id, str) and re.fullmatch(r"[0-9a-f]{64}", response_id), "响应编号无效")
    source = store.home / "responses" / (response_id + ".json")
    require(source.is_file(), "未找到该响应", "not_found", "使用原任务或操作编号核对记录")
    content = source.read_bytes()
    require(digest(content) == response_id, "响应内容与原摘要不一致", "response_corrupt", "读取原任务或操作记录重新核对")
    return operation_page(json.loads(content), path, offset, limit, text_limit,
                          record_id=response_id, read_method="response_read", id_key="response_id")


def bound_error(store, method, error):
    if size(error) <= 5500:
        return error
    saved = bound_response(store, method, error)
    return {**error, "message": str(error["message"])[:800],
            "next_action": "读取完整错误与恢复动作：" + encode(saved["details"]["read"])}
