"""Generic schema-validated stdio calls; circuit candidates remain JSON data."""
from datetime import timedelta
import hashlib
import json
import os
import sys
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from jsonschema import Draft202012Validator


def parameters(schema,request):
    value={} if request is None else dict(request=request)
    Draft202012Validator(schema).validate(value)
    return value


async def invoke(tool=None,request=None,timeout=240):
    if type(timeout) is not int or not 1<=timeout<=360:raise ValueError('RPC_TIMEOUT_OUT_OF_RANGE')
    server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env=dict(os.environ))
    async with stdio_client(server) as (a,b),ClientSession(a,b) as session:
        await session.initialize();listing=await session.list_tools()
        schemas={t.name:t.inputSchema for t in listing.tools}
        fingerprint=hashlib.sha256(json.dumps(schemas,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if tool is None:return dict(schema_sha256=fingerprint,**listing.model_dump(mode='json'))
        if tool not in schemas:raise ValueError('TOOL_NOT_AVAILABLE: '+tool)
        params=parameters(schemas[tool],request)
        response=await session.call_tool(tool,params,read_timeout_seconds=timedelta(seconds=timeout))
        return dict(tool=tool,request=params,schema_sha256=fingerprint,
                    response=response.model_dump(mode='json'))
