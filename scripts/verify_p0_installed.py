"""Exercise P0 through an installed MCP service, optionally replaying existing logs."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import auto_lammps_mcp
from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.transport import call
from auto_lammps_mcp.verification import CHECK_VERSION


async def verify(home, source=None):
    params = StdioServerParameters(command=sys.executable, args=['-m', 'auto_lammps_mcp.cli', '--home', str(home), 'mcp', '--host', 'p0-installed-check'])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            async def invoke(method, **values):
                response = await client.call_tool(method, values)
                assert not response.isError, (method, response.content)
                return json.loads(response.content[0].text)

            async def finished(op):
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    current = await invoke('operation_get', operation_id=op['operation_id'])
                    if current['state'] in ('completed', 'failed', 'interrupted'):
                        assert current['state'] == 'completed', current
                        result = current['result']
                        if isinstance(result.get('checks'), dict):
                            checks, offset = [], 0
                            while True:
                                page = await invoke('operation_read', operation_id=op['operation_id'], path=['result', 'checks'], offset=offset, limit=50)
                                checks.extend(item['value'] for item in page['items'])
                                if not page['more']:
                                    break
                                offset = page['next_offset']
                            result['checks'] = checks
                        return result
                    await asyncio.sleep(.1)
                raise AssertionError('P0 operation timeout')

            connection = await invoke('doctor')
            assert connection['current_connection']['state'] == 'current'
            task = await invoke('task_create', title='P0 current evidence and pause', request='Check measured verdicts and preserve pause before moving to P1', requirements=[dict(id='report', description='current checked report')], op_id='p0-create')
            tid = task['id']

            async def mutate(method, op_id, **values):
                current = await invoke('task_get', task_id=tid)
                return await invoke(method, task_id=tid, revision=current['revision'], op_id=op_id, **values)

            async def put(path, text, op):
                return await mutate('file_patch', op, path=path, expected_sha256=None, old='', new=text)

            async def check(specs, op, artifacts=None):
                return await finished(await mutate('check_run', op, checks=specs, artifacts=artifacts or []))

            await put('metrics.json', '{"drift":20}', 'metrics')
            await put('report.txt', 'Observed drift is 20.', 'report')
            first = await check([dict(kind='json', path='metrics.json', bounds={'drift': {'max': 30}})], 'initial-check', ['report.txt'])
            await mutate('artifact_register', 'register', path='report.txt', sources=['metrics.json'], requirement_ids=['report'], checks=[dict(receipt_id=first['id'])])
            assert (await mutate('completion_check', 'initial-completion'))['complete']
            failed = await check([dict(kind='json', path='metrics.json', bounds={'drift': {'max': 1}})], 'failed-recheck')
            assert not failed['passed']
            assert not (await invoke('check_get', task_id=tid, receipt_id=first['id']))['current_passed']
            assert (await invoke('task_get', task_id=tid))['state'] == 'active'
            assert not (await invoke('task_get', task_id=tid))['artifacts'][0]['checks_passed']
            assert not (await invoke('research_metrics', task_id=tid))['completion_recorded']
            assert not (await mutate('completion_check', 'current-completion'))['complete']
            current = await invoke('task_get', task_id=tid)
            invalid = await client.call_tool('check_run', dict(task_id=tid, revision=current['revision'], checks=[dict(kind='json', path='metrics.json', bounds={'drift': {'maximum': 1}})], op_id='invalid-criterion'))
            assert invalid.isError
            await put('incomplete.log', 'Step Time TotEng\n0 0 -100\n1 .1 -100\n2 .2\n', 'incomplete-log')
            incomplete = await check([dict(kind='thermo', path='incomplete.log', min_rows=2, max_abs_drift=1)], 'incomplete-check')
            assert not incomplete['passed']
            await mutate('task_control', 'pause', action='pause')
            held = await mutate('project_export', 'held-export')
            diagnostic = await check([dict(kind='file', path='metrics.json')], 'paused-diagnostic')
            assert diagnostic['passed']
            assert (await invoke('operation_get', operation_id=held['operation_id']))['state'] == 'pending'
            assert (await invoke('task_get', task_id=tid))['state'] == 'paused'
            await mutate('task_control', 'resume', action='resume')
            await finished(held)
            replay = None
            if source:
                original = await invoke('task_create', title='P0 retained W failure evidence', request='Measure existing logs using the fixed P0 checks', requirements=[dict(id='evidence', description='complete measured evidence')], op_id='p0-existing-logs')
                imported = await invoke('project_import', task_id=original['id'], revision=original['revision'], source=str(source), op_id='import-existing-logs')
                await finished(imported)
                async def measured(specs, op):
                    current = await invoke('task_get', task_id=original['id'])
                    return await finished(await invoke('check_run', task_id=original['id'], revision=current['revision'], checks=specs, op_id=op))
                failed_log = await measured([dict(kind='thermo', path='failed/log.lammps', block=1, min_rows=2, energy_column='TotEng', max_abs_drift=1, time_column='Time', unit='eV', basis='P0 replay of retained numerical failure')], 'retained-failed')
                assert not failed_log['passed']
                injection = await measured([dict(kind='energy_jump', path='corrected/log.lammps', before_block=0, after_block=1, expected=10000, tolerance=.1, unit='eV')], 'retained-injection')
                assert injection['passed']
                drift = await measured([dict(kind='thermo', path='corrected/log.lammps', block=i, min_rows=2, energy_column='TotEng', max_abs_drift=1, time_column='Time', unit='eV', basis='P0 check against retained output precision') for i in range(1, 5)], 'retained-corrected')
                assert drift['passed']
                replay = dict(task_id=original['id'], failed=failed_log, injection=injection, corrected=drift)
            return dict(version=auto_lammps_mcp.__version__, package_path=auto_lammps_mcp.__file__, checker_version=CHECK_VERSION, task_id=tid,
                        checks=['current_measured_review', 'invalid_criterion_rejected', 'incomplete_sample_rejected', 'paused_queue_held', 'diagnostics_available_while_paused', 'resume_retains_operation'],
                        existing_log_replay=replay, physics_submissions=0, model_calls=0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path)
    parser.add_argument('--home', type=Path)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    temporary = tempfile.TemporaryDirectory(prefix='alm-p0-') if args.home is None else None
    home = args.home.resolve() if args.home else Path(temporary.name) / 'state'
    assert not (home / 'service.sock').exists(), 'Use a separate P0 acceptance directory'
    process = subprocess.Popen([sys.executable, '-m', 'auto_lammps_mcp.cli', '--home', str(home), 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                call(home, 'doctor')
                break
            except Fault:
                assert process.poll() is None, process.communicate()[1].decode()
                time.sleep(.05)
        else:
            raise AssertionError('P0 installed service failed to start')
        result = asyncio.run(verify(home, args.source.resolve() if args.source else None))
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.receipt:
            args.receipt.write_text(text)
        print(text)
    finally:
        process.terminate()
        process.communicate(timeout=10)
        if temporary:
            temporary.cleanup()


if __name__ == '__main__':
    main()
