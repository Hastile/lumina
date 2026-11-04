"""Windows integration: MCP starts the real GUI, and GUI outlives MCP disconnect."""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

work = Path(tempfile.mkdtemp(prefix='lumina-mcp-auto-', dir=Path(os.environ['LOCALAPPDATA']) / 'Temp'))
print(f'Test home: {work}', flush=True)
os.environ['LUMINA_HOME'] = str(work)
os.environ['LUMINA_RECENT'] = str(work / 'recent.json')


async def run():
    from lumina.engine import Engine
    from lumina.client import _remote

    fixture = Engine(run_jobs=False)
    target = str(work / 'target.lumina')
    fixture.dispatch('execute', {'name': 'project.rename', 'params': {'name': 'auto started target'}})
    fixture.dispatch('project.save', {'path': target})
    command = os.environ.get('LUMINA_TEST_MCP_EXE') or sys.executable
    args = ['mcp'] if os.environ.get('LUMINA_TEST_MCP_EXE') else ['-m', 'lumina', 'mcp']
    server = StdioServerParameters(command=command, args=args, env=dict(os.environ))
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(name, arguments=None):
                result = await session.call_tool(name, arguments or {})
                assert not result.is_error, result.content
                return json.loads(next(c.text for c in result.content if c.type == 'text'))

            assert not (await call('get_editor_status'))['connected']
            assert not (work / 'engine.json').exists(), 'MCP startup must not spawn an engine'
            status = await call('ensure_editor', {'project_path': target})
            assert status['case'] == 'gui_started' and status['connected'] and not status['headless']
            assert (await call('get_project'))['name'] == 'auto started target'
            assert (await call('ensure_editor', {'project_path': target}))['case'] == 'project_already_open'
    # No MCP connection remains: its GUI and target must still be there.
    remote = _remote()
    assert remote is not None
    try:
        assert remote.dispatch('project.summary')['name'] == 'auto started target'
    finally:
        remote.close()
    print('PASS: real stdio MCP starts GUI + target, reuses target, GUI survives MCP disconnect.')


def cleanup_test_gui():
    discovery = work / 'engine.json'
    if not discovery.exists():
        return
    pid = int(json.loads(discovery.read_text(encoding='utf-8'))['pid'])
    expected = Path(os.environ['LUMINA_EDITOR_EXE']).resolve()
    # Terminate only the test engine's ancestor whose executable is exactly the
    # configured GUI. This isolated profile never opens the user's projects.
    for _ in range(5):
        command = f'Get-CimInstance Win32_Process -Filter "ProcessId={pid}" | Select-Object ProcessId,ParentProcessId,ExecutablePath | ConvertTo-Json -Compress'
        result = subprocess.run(['powershell', '-NoProfile', '-Command', command], capture_output=True, text=True, check=True)
        if not result.stdout.strip():
            return
        process = json.loads(result.stdout)
        if process.get('ExecutablePath') and Path(process['ExecutablePath']).resolve() == expected:
            os.kill(pid, signal.SIGTERM)
            return
        pid = int(process['ParentProcessId'])
    raise RuntimeError('Could not verify the isolated GUI process for cleanup')


try:
    assert os.environ.get('LUMINA_EDITOR_EXE'), 'Set LUMINA_EDITOR_EXE to the GUI under test'
    asyncio.run(run())
finally:
    cleanup_test_gui()
