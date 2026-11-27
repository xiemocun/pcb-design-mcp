"""Real stdio MCP smoke test against an installed ``pcb-design-mcp``.

Run it with the wheel installed and the working directory OUTSIDE the source
tree. It starts the server over stdio, checks the handshake and tool
discovery, creates a throwaway project in a temporary data directory, reads
it back and finds it in ``project_list``. Prints a JSON summary and
``STDIO SMOKE PASS`` on success; any failed assertion exits non-zero.

Usage: python scripts/stdio_smoke.py
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run() -> dict:
    data_dir = tempfile.mkdtemp(prefix='pcb-stdio-smoke-')
    command = shutil.which('pcb-design-mcp') or str(Path(sys.executable).parent / 'pcb-design-mcp')
    params = StdioServerParameters(command=command,
                                   env={**os.environ, 'PCB_MCP_DATA_DIR': data_dir})
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {'project_create', 'project_read', 'project_list'} <= names, names
                created = await session.call_tool(
                    'project_create',
                    {'request': {'name': 'stdio-smoke', 'profile': 'schematic_experiment'}})
                assert not created.isError, created.content
                payload = json.loads(created.content[0].text)
                project_id = payload['project_id']
                read_back = await session.call_tool(
                    'project_read', {'request': {'project_id': project_id}})
                assert not read_back.isError, read_back.content
                state = json.loads(read_back.content[0].text)
                assert state['ok'] and state['data']['id'] == project_id
                assert state['data']['name'] == 'stdio-smoke'
                listed = await session.call_tool('project_list', {})
                assert not listed.isError, listed.content
                projects = json.loads(listed.content[0].text)['data']['projects']
                assert any(p['project_id'] == project_id for p in projects)
                return dict(server=init.serverInfo.name, tools=len(names),
                            project_id=project_id, read_back=True, listed=True)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == '__main__':
    print(json.dumps(asyncio.run(run())))
    print('STDIO SMOKE PASS')
