"""Read executable/library bytes without invoking a scientific engine.

This standalone helper is also copied into accounted jobs. A changed runtime
stops the job before its native research entry is started.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys

PROBE_VERSION = 1


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def program_identity(identity):
    """Login-node system libraries are observations, not compute-node evidence."""
    return {key: identity.get(key, {}) for key in ('commands', 'files')}


def differences(expected, actual):
    return [group + ':' + path for group in ('commands', 'files', 'libraries')
            for path in sorted(set(expected.get(group, {})) | set(actual.get(group, {})))
            if expected.get(group, {}).get(path) != actual.get(group, {}).get(path)]


def describe(path):
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_file():
        raise ValueError('Runtime dependency is not a regular file: ' + str(path))
    digest = hashlib.sha256()
    with resolved.open('rb') as stream:
        before = os.fstat(stream.fileno())
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Runtime dependency changed while reading: ' + str(path))
    return dict(path=str(resolved), sha256=digest.hexdigest(), bytes=after.st_size)


def inspect(spec):
    commands, files, libraries = {}, {}, {}
    for command in spec['commands']:
        path = shutil.which(command)
        if not path:
            raise ValueError('Runtime command not found: ' + command)
        commands[command] = describe(path)
    for path in spec.get('files', []):
        files[path] = describe(path)
    # ldd resolves the shared libraries of installed, trusted ELF programs;
    # it does not run a simulation. Scripts and static binaries have no map.
    for item in list(commands.values()) + list(files.values()):
        with open(item['path'], 'rb') as stream:
            elf = stream.read(4) == b'\x7fELF'
        if not elf:
            continue
        result = subprocess.run(['ldd', item['path']], capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        if 'not found' in output:
            raise ValueError('Unresolved runtime library: ' + output)
        if result.returncode and not any(x in output for x in ('not a dynamic executable', 'statically linked')):
            raise ValueError('Cannot resolve runtime libraries: ' + output)
        for line in output.splitlines():
            match = re.search(r'(?:=>\s*)?(/\S+)\s+\(', line)
            if match:
                path = match[1]
                libraries[path] = describe(path)
    identity = dict(probe_version=PROBE_VERSION, commands=commands, files=files, libraries=libraries)
    return dict(identity=identity, fingerprint=fingerprint(identity),
                host=socket.gethostname(), role=spec.get('role', 'login'),
                job_id=os.environ.get('SLURM_JOB_ID') if spec.get('role') == 'compute' else None)


def main():
    spec = json.loads(Path(sys.argv[1]).read_text())
    try:
        measured = inspect(spec)
        expected_programs = spec.get('programs')
        measured['differences'] = differences(expected_programs or {}, program_identity(measured['identity'])) if expected_programs else []
        if spec.get('expected_identity'):
            measured['differences'] += differences(spec['expected_identity'], measured['identity'])
        measured['passed'] = not measured['differences'] and (not spec.get('expected') or measured['fingerprint'] == spec['expected'])
    except Exception as exc:
        measured = dict(passed=False, error=str(exc))
    Path(sys.argv[2]).write_text(json.dumps(measured, sort_keys=True) + '\n')
    return 0 if measured['passed'] else 78


if __name__ == '__main__':
    raise SystemExit(main())
