"""Native files and content-addressed snapshots; no scientific input schema."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .core import digest, encode, identifier, require


def file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".alm-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def manifest(store, task_id, paths=None):
    root = store.root(task_id)
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            require(path.resolve().is_relative_to(root), f"项目链接指向外部: {path.relative_to(root)}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if paths is not None and not any(relative == selected or relative.startswith(selected.rstrip("/") + "/") for selected in paths):
            continue
        sha = file_hash(path)
        blob = store.home / "blobs" / sha
        if not blob.exists():
            # Copy before publishing. Verify in case an editor changed the source mid-copy.
            temporary = blob.with_name(sha + "." + identifier("copy"))
            shutil.copyfile(path, temporary)
            require(file_hash(temporary) == sha, f"读取期间文件发生变化: {relative}", "file_changed", "保存文件后重新建立快照")
            os.replace(temporary, blob)
        result[relative] = dict(sha256=sha, bytes=path.stat().st_size, executable=bool(path.stat().st_mode & 0o111))
    return result


def invalidate(store, db, task_id, current):
    rows = db.execute("SELECT * FROM artifacts WHERE task_id=? AND stale=0", (task_id,)).fetchall()
    stale_paths = {r[0] for r in db.execute("SELECT path FROM artifacts WHERE task_id=? AND stale=1", (task_id,))}
    remaining = list(rows)
    while remaining:
        changed = []
        for row in remaining:
            sources = json.loads(row["sources"])
            all_files = {**sources, row["path"]: row["sha256"]}
            if set(sources) & stale_paths or any(current.get(name, {}).get("sha256") != sha for name, sha in all_files.items()):
                db.execute("UPDATE artifacts SET stale=1 WHERE id=?", (row["id"],))
                store.event(db, task_id, "artifact_stale", dict(artifact_id=row["id"]))
                stale_paths.add(row["path"])
                changed.append(row)
        if not changed:
            break
        remaining = [r for r in remaining if r not in changed]


def import_directory(store, task_id, source):
    source = Path(source).expanduser().resolve()
    target = store.root(task_id)
    require(source.is_dir(), "导入目录不存在")
    require(not source.is_relative_to(target) and not target.is_relative_to(source), "源目录和项目目录不能相互包含")
    files = []
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if ".git" in relative.parts:
            continue
        require(not item.is_symlink() or item.resolve().is_relative_to(source), f"请先收集外部引用: {relative}")
        if item.is_file():
            destination = store.path(task_id, relative.as_posix())
            sha = file_hash(item)
            require(not destination.exists() or file_hash(destination) == sha, f"导入文件与现有内容冲突: {relative}", "file_conflict", "导入到新任务或先查看差异")
            files.append((item, destination, sha))
    for item, destination, sha in files:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_name(destination.name + ".alm-import")
            shutil.copy2(item, temporary)
            require(file_hash(temporary) == sha, "导入期间源文件改变", "file_changed", "保存源文件后重试")
            os.replace(temporary, destination)
    return dict(files=len(files), project_path=str(target), source=str(source), git_directory="excluded")


def patch_file(store, task_id, path, expected_sha256, old, new):
    target = store.path(task_id, path)
    exists = target.exists()
    before = target.read_bytes() if exists else b""
    require((digest(before) if exists else None) == expected_sha256, "文件内容已变化", "file_conflict", "重新读取文件并计算局部修改")
    text = before.decode("utf-8")
    if exists:
        require(bool(old) and text.count(old) == 1, "替换片段必须恰好匹配一次")
        after = text.replace(old, new, 1)
    else:
        require(old == "", "新文件的 old 应为空")
        after = new
    # Preserve old content even if the task transaction is interrupted.
    if exists:
        atomic_write(store.home / "blobs" / digest(before), before)
    atomic_write(target, after.encode())
    return dict(path=path, before_sha256=expected_sha256, sha256=digest(after.encode()),
                diff="".join(difflib.unified_diff(text.splitlines(True), after.splitlines(True), fromfile=path, tofile=path)))


def materialize(store, snapshot, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name, info in snapshot.items():
        path = destination / name
        require(not Path(name).is_absolute() and ".." not in Path(name).parts, "快照路径无效")
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = store.home / "blobs" / info["sha256"]
        require(file_hash(blob) == info["sha256"], "快照文件损坏", "integrity_error", "从备份恢复对应文件")
        shutil.copyfile(blob, path)
        path.chmod(0o700 if info.get("executable") else 0o600)

