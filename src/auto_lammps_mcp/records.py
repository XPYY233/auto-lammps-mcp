"""Readable task summaries and live paths for restored historical receipts."""
import json
from pathlib import Path


def decoded_record(row):
    record = dict(row)
    for key in ("environment", "rules", "evidence", "sources", "requirements", "checks",
                "payload", "result", "dependencies", "specification", "data"):
        if isinstance(record.get(key), str):
            record[key] = json.loads(record[key])
    return record


def restored_paths(store, value):
    recovery = store.setting("recovery", {})
    if not recovery.get("source_home"):
        return value
    sources = [Path(root) for root in {*recovery.get("source_homes", []), recovery["source_home"]}]
    sources.sort(key=lambda root: len(root.parts), reverse=True)
    path_keys = {"path", "absolute_path", "directory", "project_path", "log", "source"}

    def visit(item):
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, dict):
            return item
        result = {key: visit(child) if key != "original_paths" else child for key, child in item.items()}
        for key in path_keys & item.keys():
            path = item[key]
            if not isinstance(path, str) or not Path(path).is_absolute():
                continue
            for source in sources:
                try:
                    relative = Path(path).relative_to(source)
                except ValueError:
                    continue
                if relative.parts and relative.parts[0] in ("projects", "exports", "blobs", "logs"):
                    result[key] = str(store.home / relative)
                    result.setdefault("original_paths", {})[key] = path
                    break
        return result

    return visit(value)


def brief(data):
    if not isinstance(data, dict):
        return data
    keys = {"operation_id", "kind", "run_id", "job_id", "state", "stage_id", "artifact_id", "path", "filename", "revision", "reason", "next_action", "complete", "missing", "active_runs", "active_work", "confirmed_at", "last_confirmed"}
    result = {key: value for key, value in data.items() if key in keys}
    for key in ("error", "message"):
        if key in data:
            result[key] = str(data[key])[:600]
    if isinstance(data.get("result"), dict):
        result["result"] = brief(data["result"])
    if "files" in data:
        result["file_count"] = len(data["files"]) if isinstance(data["files"], (dict, list)) else data["files"]
    return result


def task_summary(task):
    recent_limit = 5
    task["records_truncated"] = [name for name in task["record_counts"] if task["record_counts"][name] > recent_limit]
    for name in task["record_counts"]:
        if name in ("work", "events"):
            task[name] = task[name][:recent_limit]
        elif name == "runs":
            active = [run for run in task[name] if run["state"] in ("dispatching", "unknown", "queued", "running", "cancelling")]
            rest = [run for run in task[name] if run not in active]
            task[name] = active + rest[-(recent_limit-len(active)):] if len(active) < recent_limit else active[:recent_limit]
        else:
            task[name] = task[name][-recent_limit:]
    for run in task["runs"]:
        progress = run["evidence"].get("output_progress")
        if progress:
            run["evidence"]["output_progress"] = {**progress, "files": [{key: value for key, value in file.items() if key != "text"} for file in progress["files"]]}
        run.pop("environment", None)
        run.pop("rules", None)
    for artifact in task["artifacts"]:
        artifact["checks_passed"] = all(check["passed"] for check in artifact.pop("checks"))
        artifact["source_count"] = len(artifact.pop("sources"))
    for work in task["work"]:
        if work.get("result"):
            work["result"] = brief(json.loads(work["result"]))
    for event in task["events"]:
        event["data"] = brief(json.loads(event["data"]))
    for stage in task["stages"]:
        if stage.get("result"):
            stage["result"] = brief(json.loads(stage["result"]))
    task["details"] = dict(records="task_records", history="events", operations="operation_get", runs="run_get")
    return task
