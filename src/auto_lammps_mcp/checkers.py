"""Qualify native analysis checkers against independent positive/negative fixtures."""
import json

from .core import require
from .files import file_hash


def analysis_evidence(store, db, task_id, operation_id, output):
    row = db.execute("SELECT * FROM queue WHERE id=? AND task_id=? AND kind='analysis' AND state='completed'", (operation_id, task_id)).fetchone()
    require(row, '检查程序需要已完成的原生分析操作')
    result = json.loads(row['result'])
    require(output in result['outputs'], '输出未在分析回执中登记')
    relative = 'analysis/' + operation_id + '/' + output
    file = store.path(task_id, relative)
    require(file.is_file() and file_hash(file) == result['outputs'][output]['sha256'], '分析输出已变化')
    script = result['script']
    require(script in result['sources'], '分析回执缺少程序来源')
    return dict(path=relative, script_sha256=result['sources'][script], sources=result['sources'],
                script=script, result=result)
