"""Every exposed tool is tested offline; failures remain MCP errors. [AI-Codex]"""

import json
from contextlib import asynccontextmanager

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from cdash_mcp import server as s

CASES = [
    ("get_test_images", {"build_test_id": 10}),
    ("check_connection", {"project": "thor"}),
    ("get_dashboard", {"project": "thor"}),
    ("get_failing_tests", {"project": "thor"}),
    ("get_build_details", {"build_id": 1}),
    ("get_build_errors", {"build_id": 1}),
    ("get_build_tests", {"build_id": 1}),
    ("get_configure_output", {"build_id": 1}),
    ("get_test_details", {"build_test_id": 10}),
    ("get_test_summary", {"project": "thor", "test_name": "fixed"}),
    ("get_build_update", {"build_id": 1}),
    ("get_update_files", {"build_id": 1}),
    ("get_project_overview", {"project": "thor"}),
    ("get_build_coverage", {"build_id": 1}),
    ("get_coverage_file", {"build_id": 1, "path": "src/a.cpp"}),
    ("compare_build_coverage", {"base_build_id": 1, "compare_build_id": 2}),
    ("get_coverage_comparison", {"project": "thor", "build_id": 1}),
    ("get_dynamic_analysis", {"build_id": 1}),
    ("get_dynamic_analysis_details", {"analysis_id": 1}),
    ("search_builds", {"project": "thor"}),
    ("get_test_history", {"project": "thor", "test_name": "fixed"}),
    ("compare_builds", {"base_build_id": 1, "compare_build_id": 2}),
    ("get_build_triage", {"build_id": 1}),
    ("get_build_notes", {"build_id": 1}),
    ("get_build_artifacts", {"build_id": 1}),
    ("get_build_commands", {"build_id": 1}),
]


@pytest.mark.parametrize("name,args", CASES)
async def test_every_tool(name, args, ctx):
    result = await getattr(s, name)(**args, ctx=ctx)
    assert isinstance(result, dict)
    assert "Error" not in result


async def test_tool_registration():
    tools = await s.mcp.list_tools()
    assert {t.name for t in tools} == {name for name, _ in CASES}
    assert all(t.annotations.readOnlyHint and not t.annotations.destructiveHint for t in tools)


async def test_ids_and_urls(ctx):
    result = await s.get_failing_tests("thor", ctx=ctx)
    item = result["items"][0]
    assert item["build_id"] == 1 and item["test_id"] == 10
    assert item["test_url"] == "https://cdash.test/tests/10"


async def test_long_compiler_message_is_retrievable(ctx):
    result = await s.get_build_errors(1, output_offset=500, output_limit=300, ctx=ctx)
    message = result["items"][0]["stdError"]
    assert message == {
        "text": "x" * 300,
        "total_characters": 1000,
        "offset": 500,
        "next_offset": 800,
    }
    full = await s.get_build_errors(1, output_limit=0, ctx=ctx)
    assert len(full["items"][0]["stdError"]["text"]) == 1000


async def test_configure_and_test_output_slicing(ctx):
    cfg = await s.get_configure_output(1, output_offset=2, output_limit=3, ctx=ctx)
    test = await s.get_test_details(10, output_offset=2, output_limit=3, ctx=ctx)
    assert cfg["configure"]["log"]["text"] == test["output"]["text"] == "234"
    assert test["output"]["next_offset"] == 5


async def test_compare_builds_semantics(ctx):
    result = await s.compare_builds(1, 2, ctx=ctx)
    assert result["new_failures"] == ["new"]
    assert result["fixed_failures"] == ["fixed"]
    assert result["total_test_changes"] == 2


async def test_coverage_weighted_totals_and_added_files(ctx):
    result = await s.compare_build_coverage(1, 2, ctx=ctx)
    assert result["base_totals"]["percent"] == 25
    assert result["compare_totals"]["percent"] == pytest.approx(92 / 104 * 100)
    assert result["changed_files"][0]["percentage_point_change"] == 25
    assert result["changed_files"][1]["base"] is None


async def test_build_search_filters_use_variables(ctx, cdash_api):
    await s.search_builds(
        "thor",
        name='quote"',
        start_date="2026-10-01",
        end_date="2026-10-02",
        revision="abc",
        ctx=ctx,
    )
    payload = json.loads(cdash_api[1][-1].content)
    assert 'quote"' not in payload["query"]
    assert {"contains": {"name": 'quote"'}} in payload["variables"]["filters"]["all"]
    assert {"lt": {"startTime": "2026-10-03T00:00:00Z"}} in payload["variables"]["filters"]["all"]


async def test_artifact_download_url(ctx):
    result = await s.get_build_artifacts(1, ctx=ctx)
    assert result["items"][0]["download_url"] == "https://cdash.test/builds/1/files/1"
    assert (await s.get_build_artifacts(1, kind="urls", ctx=ctx))["items"][0]["href"]


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_build_tests", {"build_id": 1, "status_filter": "bad"}),
        ("get_test_details", {"build_test_id": 10, "output_limit": -1}),
        ("get_build_artifacts", {"build_id": 1, "kind": "bad"}),
        ("get_coverage_comparison", {"project": "thor"}),
        (
            "search_builds",
            {"project": "thor", "start_date": "2026-10-02", "end_date": "2026-10-01"},
        ),
    ],
)
async def test_validation_is_tool_error(name, args, ctx):
    with pytest.raises(ToolError):
        await getattr(s, name)(**args, ctx=ctx)


async def test_mcp_error_flag_and_transport(client, monkeypatch):
    # Exercise the actual MCP client/server protocol with a deterministic client.
    import anyio
    from mcp.client.session import ClientSession
    from mcp.shared.message import SessionMessage

    @asynccontextmanager
    async def fake_lifespan(server):
        yield {"client": client}

    monkeypatch.setattr(s.mcp._mcp_server, "lifespan", fake_lifespan)
    read_w, read = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write, write_r = anyio.create_memory_object_stream[SessionMessage](0)
    async with anyio.create_task_group() as tg:
        tg.start_soon(
            s.mcp._mcp_server.run, read, write, s.mcp._mcp_server.create_initialization_options()
        )
        async with ClientSession(write_r, read_w) as session:
            await session.initialize()
            result = await session.call_tool("get_coverage_comparison", {"project": "thor"})
            assert result.isError
            assert "Supply build_id" in result.content[0].text
            good = await session.call_tool("get_build_tests", {"build_id": 1, "limit": 1})
            assert not good.isError
            assert "fixed" in good.content[0].text
        tg.cancel_scope.cancel()


async def test_duplicate_test_names_fail_comparison(ctx, client, monkeypatch):
    async def duplicate_rows(*args, **kwargs):
        return [{"name": "duplicate"}, {"name": "duplicate"}]

    monkeypatch.setattr(client, "all_items", duplicate_rows)
    with pytest.raises(ToolError, match="Duplicate test names"):
        await s.compare_builds(1, 2, ctx=ctx)


async def test_date_overflow_is_tool_error(ctx):
    with pytest.raises(ToolError, match="end_date"):
        await s.search_builds("thor", end_date="9999-12-31", ctx=ctx)


async def test_negative_ids_fail_before_http(ctx, cdash_api):
    with pytest.raises(ToolError, match="positive"):
        await s.get_dynamic_analysis_details(-1, ctx=ctx)
    with pytest.raises(ToolError, match="positive"):
        await s.get_update_files(-1, ctx=ctx)
    assert cdash_api[1] == []


@pytest.mark.parametrize("bad_path", [None, 42, "", "   "])
@pytest.mark.parametrize("affected_build", [1, 2])
async def test_coverage_comparison_rejects_unusable_paths(
    ctx,
    client,
    monkeypatch,
    bad_path,
    affected_build,
):
    original = client.all_items

    async def coverage_with_unusable_path(parent, build_id, relation, fields, **kwargs):
        rows = await original(parent, build_id, relation, fields, **kwargs)
        if build_id == affected_build:
            rows.append({**rows[0], "id": "bad-path-record", "filePath": bad_path})
        return rows

    monkeypatch.setattr(client, "all_items", coverage_with_unusable_path)
    with pytest.raises(ToolError, match=f"bad-path-record.*build {affected_build}.*file path"):
        await s.compare_build_coverage(1, 2, ctx=ctx)
