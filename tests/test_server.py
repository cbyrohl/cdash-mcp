"""MCP tool tests using in-process memory streams. [AI-Claude]"""

import pytest
import anyio

from mcp.client.session import ClientSession
from mcp.shared.message import SessionMessage


PROJECT = "PublicDashboard"


async def _forward(reader, writer):
    """Forward messages from reader stream to writer stream."""
    async for msg in reader:
        await writer.send(msg)


async def _run_with_client(fn):
    """Set up MCP client/server, run fn(client), then tear down cleanly."""
    from cdash_mcp.server import mcp as fastmcp

    # Server-side streams
    s_read_w, s_read = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    s_write, s_write_r = anyio.create_memory_object_stream[SessionMessage](0)

    # Client-side streams
    c_write, c_write_r = anyio.create_memory_object_stream[SessionMessage](0)
    c_read_w, c_read = anyio.create_memory_object_stream[SessionMessage | Exception](0)

    async with anyio.create_task_group() as tg:
        init_opts = fastmcp._mcp_server.create_initialization_options()
        tg.start_soon(fastmcp._mcp_server.run, s_read, s_write, init_opts)
        tg.start_soon(_forward, c_write_r, s_read_w)
        tg.start_soon(_forward, s_write_r, c_read_w)

        async with ClientSession(c_read, c_write) as client:
            await client.initialize()
            await fn(client)

        # Signal shutdown
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_list_tools():
    """Server exposes all 12 tools. [AI]"""

    async def check(client):
        result = await client.list_tools()
        tool_names = {t.name for t in result.tools}
        expected = {
            "get_dashboard",
            "get_failing_tests",
            "get_build_details",
            "get_build_errors",
            "get_build_tests",
            "get_configure_output",
            "get_test_details",
            "get_test_summary",
            "get_build_update",
            "get_project_overview",
            "get_coverage_comparison",
            "get_dynamic_analysis",
        }
        assert expected == tool_names

    await _run_with_client(check)


@pytest.mark.anyio
async def test_get_dashboard_tool():
    """get_dashboard tool returns formatted dashboard text. [AI]"""

    async def check(client):
        result = await client.call_tool(
            "get_dashboard", {"project": PROJECT}
        )
        assert not result.isError
        text = result.content[0].text
        assert "Dashboard" in text

    await _run_with_client(check)


@pytest.mark.anyio
async def test_get_failing_tests_tool():
    """get_failing_tests tool returns formatted test results. [AI]"""

    async def check(client):
        result = await client.call_tool(
            "get_failing_tests", {"project": PROJECT}
        )
        assert not result.isError
        text = result.content[0].text
        assert "Failing Tests" in text

    await _run_with_client(check)


@pytest.mark.anyio
async def test_get_dashboard_invalid_project():
    """get_dashboard with invalid project returns graceful response. [AI]"""

    async def check(client):
        result = await client.call_tool(
            "get_dashboard", {"project": "NonExistentProject12345"}
        )
        # CDash may return empty data or error - tool should handle gracefully
        assert not result.isError
        text = result.content[0].text
        assert isinstance(text, str)

    await _run_with_client(check)
