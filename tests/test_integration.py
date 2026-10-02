"""Explicitly opt-in smoke checks against a configured CDash project. [AI-Codex]"""

import os
from types import SimpleNamespace

import pytest

from cdash_mcp.client import CDashClient
from cdash_mcp.server import check_connection

pytestmark = pytest.mark.integration


async def test_live_build_and_test_navigation():
    project = os.getenv("CDASH_LIVE_PROJECT")
    if not project:
        pytest.skip("Set CDASH_LIVE_PROJECT, CDASH_URL and optional CDASH_TOKEN.")
    async with CDashClient() as client:
        ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"client": client}))
        status = await check_connection(project, ctx)
        assert status["project"]["name"] == project
        dashboard = await client.get_dashboard(project)
        builds = [b for g in dashboard["buildgroups"] for b in g.get("builds", [])]
        assert builds, "Choose a live project/date with submitted builds."
        build_id = int(builds[0]["id"])
        summary = await client.get_build_summary(build_id)
        assert summary["id"] == str(build_id)
        await client.get_configure(build_id)
        await client.get_build_errors(build_id, limit=1)
        await client.get_build_update(build_id)
        page = await client.get_build_tests(build_id, limit=1)
        if page["items"]:
            details = await client.get_test_details(int(page["items"][0]["id"]))
            assert details["id"] == page["items"][0]["id"]
