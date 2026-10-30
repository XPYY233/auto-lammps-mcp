"""Read executed LAMMPS commands, retaining phase facts beside each thermo block.

Pure standard library so the node monitor and local checks use the same parser.
Selectors describe actual fixes, not model-supplied log block numbers. Unresolved
commands remain visible; this does not attempt to interpret arbitrary LAMMPS code.
"""
import copy
import math
import shlex


class PhaseTracker:
    def __init__(self):
        self.fixes = {}
        self.units = None
        self.norm = None
        self.timestep = None
        self.run_index = -1
        self.marker = None
        self.generation = 0
        self.pending = ''

    def feed(self, line):
        text = line.strip()
        if text.startswith('ALM_PHASE '):
            self.marker = text[len('ALM_PHASE '):].strip()
            return
        if text.endswith('&'):
            self.pending += text[:-1] + ' '
            return
        text, self.pending = self.pending + text, ''
        try:
            words = shlex.split(text, comments=True)
        except ValueError:
            return
        if not words:
            return
        command, args = words[0], words[1:]
        if command == 'clear':
            self.__init__()
        elif command == 'fix' and len(args) >= 3:
            self.fixes[args[0]] = dict(id=args[0], group=args[1], style=args[2], arguments=args[3:])
            self.generation += 1
        elif command == 'unfix' and len(args) == 1:
            self.fixes.pop(args[0], None)
            self.generation += 1
        elif command == 'units' and len(args) == 1:
            self.units = args[0]
        elif command == 'timestep' and len(args) == 1:
            try:
                self.timestep = float(args[0])
            except ValueError:
                self.timestep = None
        elif command == 'thermo_modify' and 'norm' in args:
            index = args.index('norm')
            self.norm = args[index + 1] if index + 1 < len(args) else None
        elif command in ('run', 'minimize'):
            # Echo can include an unresolved line followed by its expanded form.
            if args and not any('$' in x for x in args):
                self.run_index += 1

    def state(self):
        return dict(fixes=copy.deepcopy(list(self.fixes.values())), units=self.units,
                    norm=self.norm, timestep=self.timestep, run_index=self.run_index, marker=self.marker, generation=self.generation)


def matches(phase, selector):
    if not selector:
        return True
    for key in ('marker', 'run_index', 'timestep'):
        if key in selector and phase.get(key) != selector[key]:
            return False
    wanted = {k: selector[k] for k in ('fix_id', 'style', 'group') if k in selector}
    return not wanted or any(all(f.get('id' if k == 'fix_id' else k) == v for k, v in wanted.items())
                             for f in phase['fixes'])


def applicability(phase, spec):
    """Report a mismatched raw-energy assumption; don't assign a global tolerance."""
    if 'max_abs_drift' not in spec or spec.get('energy_column', 'TotEng') not in ('TotEng', 'Etotal'):
        return None
    if spec.get('balance') == 'task_specific':
        return None
    exchanges = ('nvt', 'npt', 'nph', 'langevin', 'temp/', 'heat', 'ehex', 'deform', 'viscous')
    found = [f for f in phase['fixes'] if f['style'].startswith(exchanges)]
    if found:
        return dict(reason='energy_exchange', fixes=found,
                    next_action='选择计入热量/外界做功的量，或以 basis 说明任务专属变化范围并用 balance=task_specific')
    return None


def selected(block, index, spec):
    return ('block' not in spec or spec['block'] == index) and matches(block['phase'], spec.get('phase'))


def blocks(path):
    result, current, tracker = [], None, PhaseTracker()
    with open(path, errors='replace') as stream:
        for number, line in enumerate(stream, 1):
            fields = line.split()
            if fields and fields[0] in ('Step', 'Time') and len(fields) > 1:
                current = dict(columns=fields, rows=[], first_line=number, phase=tracker.state(), errors=[])
                result.append(current)
                continue
            if current and fields:
                try:
                    float(fields[0])
                except ValueError:
                    if line.lstrip().startswith('WARNING:'):
                        continue
                    current = None
                else:
                    if len(fields) != len(current['columns']):
                        current['errors'].append(f'第 {number} 行热力学采样字段不完整')
                        continue
                    try:
                        values = [float(x) for x in fields]
                    except ValueError:
                        current['errors'].append(f'第 {number} 行包含非数值字段')
                        continue
                    if not all(math.isfinite(x) for x in values):
                        current['errors'].append(f'第 {number} 行存在非有限数据')
                        continue
                    current['rows'].append(values)
                    continue
            elif not fields:
                current = None
            tracker.feed(line)
    return result
