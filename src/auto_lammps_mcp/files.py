"""Native files and content-addressed snapshots; no scientific input schema."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import sys

from .core import digest, encode, identifier, require


def file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def clone_file(source, destination):
    """Try the macOS copy-on-write syscall; unsupported volumes use normal copying."""
    if sys.platform != "darwin":
        return False
    import ctypes
    clone = ctypes.CDLL(None, use_errno=True).clonefile
    clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    clone.restype = ctypes.c_int
    return clone(os.fsencode(source), os.fsencode(destination), 0) == 0


def copy_independent(source, destination):
    """Keep distinct editable files while sharing unchanged large blocks when supported."""
    source, destination = Path(source), Path(destination)
    if source.stat().st_size >= 1024 * 1024 and not destination.exists() and clone_file(source, destination):
        shutil.copystat(source, destination)
        return "copy_on_write"
    shutil.copy2(source, destination)
    return "copy"


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
            copy_independent(path, temporary)
            require(file_hash(temporary) == sha, f"读取期间文件发生变化: {relative}", "file_changed", "保存文件后重新建立快照")
            os.replace(temporary, blob)
        result[relative] = dict(sha256=sha, bytes=path.stat().st_size, executable=bool(path.stat().st_mode & 0o111))
    return result


def invalidate(store, db, task_id, current):
    rows = db.execute("SELECT * FROM artifacts WHERE task_id=? AND stale=0", (task_id,)).fetchall()
    latest = {r["path"]: r for r in db.execute("SELECT * FROM artifacts WHERE task_id=? ORDER BY created,rowid", (task_id,))}
    stale_paths = {path for path, row in latest.items() if row["stale"]}
    remaining = list(rows)
    while remaining:
        changed = []
        for row in remaining:
            sources = json.loads(row["sources"])
            all_files = {**sources, row["path"]: row["sha256"]}
            if set(sources) & stale_paths or any(current.get(name, {}).get("sha256") != sha for name, sha in all_files.items()):
                db.execute("UPDATE artifacts SET stale=1 WHERE id=?", (row["id"],))
                store.event(db, task_id, "artifact_stale", dict(artifact_id=row["id"]))
                if latest[row["path"]]["id"] == row["id"]:
                    stale_paths.add(row["path"])
                changed.append(row)
        if not changed:
            break
        remaining = [r for r in remaining if r not in changed]


def invalidate_paths(store, db, task_id, changed_paths):
    """Propagate a known native edit without rehashing unrelated project files."""
    affected = set(changed_paths)
    remaining = list(db.execute("SELECT * FROM artifacts WHERE task_id=? AND stale=0", (task_id,)))
    latest = {r["path"]: r["id"] for r in db.execute("SELECT id,path FROM artifacts WHERE task_id=? ORDER BY created,rowid", (task_id,))}
    while remaining:
        changed = []
        for row in remaining:
            if row["path"] in affected or set(json.loads(row["sources"])) & affected:
                db.execute("UPDATE artifacts SET stale=1 WHERE id=?", (row["id"],))
                store.event(db, task_id, "artifact_stale", dict(artifact_id=row["id"]))
                if latest[row["path"]] == row["id"]:
                    affected.add(row["path"])
                changed.append(row)
        if not changed:
            break
        remaining = [row for row in remaining if row not in changed]


def preview_stale(store, task_id, path):
    with store.connect() as db:
        latest = {r["path"]: r for r in db.execute("SELECT * FROM artifacts WHERE task_id=? ORDER BY created,rowid", (task_id,))}
    hashes, checked = {}, {}

    def current_hash(name):
        if name not in hashes:
            file = store.path(task_id, name)
            hashes[name] = file_hash(file) if file.is_file() else None
        return hashes[name]

    def visit(name, visiting):
        if name in checked:
            return checked[name]
        if name in visiting:
            return True
        row = latest.get(name)
        if row is None:
            return False
        stale = bool(row["stale"]) or current_hash(name) != row["sha256"]
        for source, sha in json.loads(row["sources"]).items():
            stale = stale or current_hash(source) != sha or (source != name and visit(source, visiting | {name}))
        checked[name] = stale
        return stale

    return visit(path, set())


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
            copy_independent(item, temporary)
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

