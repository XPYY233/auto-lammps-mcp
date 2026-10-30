import json
from pathlib import Path
import subprocess
import sys

from auto_lammps_mcp.node_guard import Watch
from auto_lammps_mcp import node_guard


def test_incremental_guard_waits_for_complete_row_and_detects_early_drift(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = Path('log.lammps')
    path.write_text('Step Time TotEng\n0 0 -100\n1 .1 -')
    watch = Watch(dict(path='log.lammps', max_abs_drift=1, block=0))
    assert watch.read() is None
    with path.open('a') as stream:
        stream.write('80\n')
    assert watch.read()['measured'] == 20


def test_guard_finishes_native_process_and_preserves_failure(tmp_path):
    (tmp_path / 'spec.json').write_text(json.dumps(dict(checks=[dict(path='log.lammps', max_abs_drift=1, action='stop')], interval_seconds=.01)))
    (tmp_path / 'entry.sh').write_text("printf 'Step TotEng\\n0 -100\\n1 -80\\n' > log.lammps\nsleep 10\n")
    result = subprocess.run([sys.executable, node_guard.__file__, 'spec.json', 'entry.sh'], cwd=tmp_path, timeout=5)
    assert result.returncode == 86
    evidence = json.loads((tmp_path / '.alm-guard.json').read_text())
    assert not evidence['passed'] and evidence['failure']['measured'] == 20
