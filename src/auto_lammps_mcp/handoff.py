"""Read exported research packages with their original evidence and native files."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile

from .core import require
from .files import file_hash, import_directory


def import_project(store, task_id, source):
    source = Path(source).expanduser().resolve()
    if source.is_dir():
        return import_directory(store, task_id, source)
    require(source.is_file() and zipfile.is_zipfile(source), "请选择项目目录或导出的研究 ZIP 包")
    with zipfile.ZipFile(source) as archive, tempfile.TemporaryDirectory(prefix="alm-handoff-") as temporary:
        names = archive.namelist()
        require(len(names) == len(set(names)), "研究包包含重复文件名", "package_invalid", "从原项目重新导出")
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            require(not path.is_absolute() and ".." not in path.parts and "\\" not in info.filename
                    and not stat.S_ISLNK(info.external_attr >> 16), "研究包包含外部路径或链接", "package_invalid", "重新导出完整项目文件")
        metadata_names = ["project-manifest.json", "research-record.json", "events.json", "README.txt"]
        require(all(name in names for name in metadata_names), "研究包缺少清单或原始记录")
        for name in metadata_names:
            require(archive.getinfo(name).file_size <= 64 * 1024 * 1024, "研究包元数据过大")
        manifest = json.loads(archive.read("project-manifest.json"))
        record = json.loads(archive.read("research-record.json"))
        require(isinstance(manifest, dict) and isinstance(record, dict) and record.get("request"), "研究包记录格式无效")
        require(set(names) == set(metadata_names) | {"project/" + name for name in manifest}, "研究包清单与实际文件不一致")
        root = Path(temporary)
        for name, expected in manifest.items():
            path = root / name
            require(not Path(name).is_absolute() and ".." not in Path(name).parts, "清单路径无效")
            info = archive.getinfo("project/" + name)
            require(info.file_size == expected["bytes"], "研究包文件大小不一致", "integrity_error", "从原项目重新导出")
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as incoming, path.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            require(file_hash(path) == expected["sha256"], "研究包文件摘要不一致", "integrity_error", "从原项目或备份恢复文件")
            path.chmod(0o700 if expected.get("executable") else 0o600)
        origin = root / "handoff" / file_hash(source)[:16]
        require(not origin.exists(), "原项目与交接记录目录冲突")
        origin.mkdir(parents=True)
        for name in metadata_names:
            (origin / name).write_bytes(archive.read(name))
        result = import_directory(store, task_id, root)
        result.update(source=str(source), source_sha256=file_hash(source), original_task=record.get("id"),
                      original_request=record["request"], original_requirements=record.get("requirements", []),
                      original_record=(origin / "research-record.json").relative_to(root).as_posix())
        return result
