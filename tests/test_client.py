"""Integration tests for CDashClient against open.cdash.org. [AI-Claude]"""

import pytest
import pytest_asyncio

from cdash_mcp.client import (
    CDashClient,
    CDashConnectionError,
    CDashNotFoundError,
)

PROJECT = "PublicDashboard"


@pytest_asyncio.fixture
async def client():
    """Create a CDashClient pointing at open.cdash.org."""
    async with CDashClient(base_url="https://open.cdash.org") as c:
        yield c


@pytest.mark.asyncio
async def test_get_dashboard(client):
    """Dashboard returns buildgroups with builds. [AI]"""
    data = await client.get_dashboard(PROJECT)
    assert "buildgroups" in data
    assert isinstance(data["buildgroups"], list)


@pytest.mark.asyncio
async def test_get_dashboard_with_date(client):
    """Dashboard accepts a date parameter. [AI]"""
    data = await client.get_dashboard(PROJECT, date="2025-01-15")
    assert "buildgroups" in data


@pytest.mark.asyncio
async def test_query_tests_not_passed(client):
    """Query tests returns builds list with test results. [AI]"""
    data = await client.query_tests(PROJECT)
    assert "builds" in data
    assert isinstance(data["builds"], list)


@pytest.mark.asyncio
async def test_query_tests_with_name_filter(client):
    """Test name filter narrows results. [AI]"""
    data = await client.query_tests(PROJECT, test_name="test")
    assert "builds" in data


@pytest.mark.asyncio
async def test_get_build_summary(client):
    """Build summary returns build info for a valid build ID. [AI]"""
    # First get a build ID from the dashboard
    dashboard = await client.get_dashboard(PROJECT)
    build_id = None
    for group in dashboard.get("buildgroups", []):
        for build in group.get("builds", []):
            build_id = build.get("id")
            if build_id:
                break
        if build_id:
            break

    if build_id is None:
        pytest.skip("No builds found on dashboard")

    data = await client.get_build_summary(int(build_id))
    assert "build" in data


@pytest.mark.asyncio
async def test_get_build_errors(client):
    """Build errors endpoint returns errors list. [AI]"""
    dashboard = await client.get_dashboard(PROJECT)
    build_id = None
    for group in dashboard.get("buildgroups", []):
        for build in group.get("builds", []):
            build_id = build.get("id")
            if build_id:
                break
        if build_id:
            break

    if build_id is None:
        pytest.skip("No builds found on dashboard")

    data = await client.get_build_errors(int(build_id))
    assert "errors" in data


@pytest.mark.asyncio
async def test_get_build_tests(client):
    """Build tests endpoint returns tests list. [AI]"""
    dashboard = await client.get_dashboard(PROJECT)
    build_id = None
    for group in dashboard.get("buildgroups", []):
        for build in group.get("builds", []):
            build_id = build.get("id")
            if build_id:
                break
        if build_id:
            break

    if build_id is None:
        pytest.skip("No builds found on dashboard")

    data = await client.get_build_tests(int(build_id))
    assert "tests" in data


@pytest.mark.asyncio
async def test_get_configure(client):
    """Configure endpoint returns configures list. [AI]"""
    dashboard = await client.get_dashboard(PROJECT)
    build_id = None
    for group in dashboard.get("buildgroups", []):
        for build in group.get("builds", []):
            build_id = build.get("id")
            if build_id:
                break
        if build_id:
            break

    if build_id is None:
        pytest.skip("No builds found on dashboard")

    data = await client.get_configure(int(build_id))
    assert "configures" in data


@pytest.mark.asyncio
async def test_connection_error():
    """Bad URL raises CDashConnectionError. [AI]"""
    async with CDashClient(base_url="https://nonexistent.invalid.example") as c:
        with pytest.raises(CDashConnectionError):
            await c.get_dashboard("Foo")
