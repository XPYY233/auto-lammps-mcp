"""Standalone compute-node log guard; no simulation or third-party dependency."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

try:
    from .phases import PhaseTracker, matches, applicability
except ImportError:
    try:
        from alm_phases import PhaseTracker, matches, applicability
    except ImportError:
        from phases import PhaseTracker, matches, applicability


class Watch:
    def __init__(self, specification):
        self.spec = specification
        self.offset = 0
        self.pending = ''
        self.columns = None
        self.block = -1
        self.reference = None
        self.rows = 0
        self.tracker = PhaseTracker()
        self.phase = self.tracker.state()
        self.issue = None
        self.coverage = []
        self.last_time = None

    def read(self):
        path = Path(self.spec['path'])
        if not path.is_file():
            return None
        with path.open() as stream:
            stream.seek(self.offset)
            text = stream.read()
            self.offset = stream.tell()
        lines = (self.pending + text).split('\n')
        self.pending = lines.pop()
        new_issue = None
        for line in lines:
            fields = line.split()
            if fields and fields[0] in ('Step', 'Time') and len(fields) > 1:
                self.columns = fields
                self.block += 1
                previous = self.phase
                self.phase = self.tracker.state()
                if not self.spec.get('phase') or previous['generation'] != self.phase['generation'] or not matches(self.phase, self.spec.get('phase')):
                    self.reference = None
                continue
            if not self.columns or len(fields) != len(self.columns):
                if self.columns and fields and matches(self.phase, self.spec.get('phase')) and ('block' not in self.spec or self.spec['block'] == self.block):
                    try:
                        float(fields[0])
                    except ValueError:
                        pass
                    else:
                        self.issue = dict(reason='incomplete_row', block=self.block, phase=self.phase)
                        new_issue = self.issue
                self.columns = None
                self.tracker.feed(line)
                continue
            try:
                values = [float(x) for x in fields]
            except ValueError:
                self.columns = None
                self.tracker.feed(line)
                continue
            if 'block' in self.spec and self.block != self.spec['block']:
                continue
            if not matches(self.phase, self.spec.get('phase')):
                continue
            mismatch = applicability(self.phase, self.spec)
            if mismatch:
                self.issue = dict(reason='inapplicable', block=self.block, phase=self.phase, detail=mismatch)
                new_issue = self.issue
                continue
            self.rows += 1
            missing = set(self.spec.get('columns', [])) - set(self.columns)
            if missing:
                self.issue = dict(reason='missing_column', columns=sorted(missing), block=self.block, phase=self.phase)
                new_issue = self.issue
                continue
            if self.block not in self.coverage:
                self.coverage.append(self.block)
            if not all(math.isfinite(x) for x in values):
                self.issue = dict(reason='nonfinite', block=self.block, row=self.rows, phase=self.phase)
                new_issue = self.issue
                continue
            if self.spec.get('time_column') in self.columns:
                value = values[self.columns.index(self.spec['time_column'])]
                if self.last_time is not None and value < self.last_time:
                    self.issue = dict(reason='time_reversed', block=self.block, phase=self.phase)
                    new_issue = self.issue
                self.last_time = value
            column = self.spec.get('energy_column', 'TotEng')
            if 'max_abs_drift' in self.spec:
                if column not in self.columns:
                    self.issue = dict(reason='missing_column', column=column, block=self.block, phase=self.phase)
                    new_issue = self.issue
                    continue
                value = values[self.columns.index(column)]
                if self.reference is None:
                    self.reference = value
                drift = abs(value - self.reference)
                if drift > self.spec['max_abs_drift']:
                    if not self.issue or drift > self.issue.get('measured', -1):
                        self.issue = dict(reason='energy_drift', block=self.block, measured=drift,
                                          threshold=self.spec['max_abs_drift'], row=self.rows, phase=self.phase)
                        new_issue = self.issue
        return new_issue


def main():
    spec = json.loads(Path(sys.argv[1]).read_text())
    watches = [Watch(c) for c in spec['checks']]
    child = subprocess.Popen(['bash', sys.argv[2]], start_new_session=True)
    def terminate(*_):
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    while True:
        failure = None
        for watch in watches:
            issue = watch.read()
            if issue and watch.spec.get('action', 'warn') == 'stop' and watch.spec.get('role', 'required') == 'required' and issue['reason'] in ('energy_drift', 'nonfinite'):
                failure = dict(**issue, path=watch.spec['path'], observed_at=time.time(), specification=watch.spec)
        report = dict(passed=all(w.rows and not w.issue for w in watches if w.spec.get('role', 'required') == 'required'),
                      failure=failure, checks=[dict(specification=w.spec, rows=w.rows, blocks=w.coverage, issue=w.issue) for w in watches],
                      observed_at=time.time())
        Path('.alm-guard.json').write_text(json.dumps(report))
        if failure:
            terminate()
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            return 86
        code = child.poll()
        if code is not None:
            # Drain output produced between the prior read and process exit.
            for w in watches:
                w.read()
            required = [w for w in watches if w.spec.get('role', 'required') == 'required']
            for w in watches:
                if w.rows < w.spec.get('min_rows', 1) and not w.issue:
                    w.issue = dict(reason='missing_monitor_samples', rows=w.rows, required_rows=w.spec.get('min_rows', 1))
            result = dict(passed=code == 0 and bool(required) and all(w.rows and not w.issue for w in required),
                          exit_code=code, failure=None, rows=[w.rows for w in watches],
                          checks=[dict(specification=w.spec, rows=w.rows, blocks=w.coverage,
                                       issue=w.issue or (dict(reason='missing_monitor_samples') if not w.rows else None)) for w in watches], observed_at=time.time())
            Path('.alm-guard.json').write_text(json.dumps(result))
            return code
        time.sleep(spec.get('interval_seconds', 2))


if __name__ == '__main__':
    raise SystemExit(main())
