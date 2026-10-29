"""Standalone compute-node log guard; no simulation or third-party dependency."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


class Watch:
    def __init__(self, specification):
        self.spec = specification
        self.offset = 0
        self.pending = ''
        self.columns = None
        self.block = -1
        self.reference = None
        self.rows = 0

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
        for line in lines:
            fields = line.split()
            if fields and fields[0] in ('Step', 'Time') and len(fields) > 1:
                self.columns = fields
                self.block += 1
                self.reference = None
                continue
            if not self.columns or len(fields) != len(self.columns):
                self.columns = None
                continue
            try:
                values = [float(x) for x in fields]
            except ValueError:
                self.columns = None
                continue
            if 'block' in self.spec and self.block != self.spec['block']:
                continue
            self.rows += 1
            if not all(math.isfinite(x) for x in values):
                return dict(reason='nonfinite', block=self.block, row=self.rows)
            column = self.spec.get('energy_column', 'TotEng')
            if 'max_abs_drift' in self.spec:
                if column not in self.columns:
                    return dict(reason='missing_column', column=column, block=self.block)
                value = values[self.columns.index(column)]
                if self.reference is None:
                    self.reference = value
                drift = abs(value - self.reference)
                if drift > self.spec['max_abs_drift']:
                    return dict(reason='energy_drift', block=self.block, measured=drift, threshold=self.spec['max_abs_drift'], row=self.rows)
        return None


def main():
    spec = json.loads(Path(sys.argv[1]).read_text())
    watches = [Watch(c) for c in spec['checks']]
    child = subprocess.Popen(['bash', sys.argv[2]], start_new_session=True)
    def terminate(*_):
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    failure = None
    while True:
        for watch in watches:
            failure = watch.read()
            if failure:
                failure.update(path=watch.spec['path'], observed_at=time.time(), specification=watch.spec)
                break
        if failure:
            Path('.alm-guard.json').write_text(json.dumps(dict(passed=False, failure=failure)))
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
            failure = next((f for w in watches if (f := w.read())), None)
            if not failure and code == 0 and any(w.rows == 0 for w in watches):
                failure = dict(reason='missing_monitor_samples')
            result = dict(passed=code == 0 and failure is None, exit_code=code, failure=failure,
                          rows=[w.rows for w in watches], observed_at=time.time())
            Path('.alm-guard.json').write_text(json.dumps(result))
            return 86 if failure else code
        time.sleep(spec.get('interval_seconds', 2))


if __name__ == '__main__':
    raise SystemExit(main())
