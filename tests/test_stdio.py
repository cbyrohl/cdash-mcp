"""STDIO transport integration tests for MCP clients such as Codex."""

import sys

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.mark.anyio
async def test_stdio_transport_lists_tools():
    """The installed module starts over STDIO and exposes its complete tool set."""
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cdash_mcp"],
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as client:
            initialized = await client.initialize()
            tools = await client.list_tools()

    assert initialized.serverInfo.name == "cdash-mcp"
    assert len(tools.tools) == 11
    assert "get_dashboard" in {tool.name for tool in tools.tools}
