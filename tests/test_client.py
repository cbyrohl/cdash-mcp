"""Client protocol, authentication and pagination regressions. [AI-Codex]"""

import json

import httpx
import pytest

from cdash_mcp.client import (
    CDashAuthError,
    CDashConnectionError,
    CDashError,
    CDashNotFoundError,
    validate_date,
    validate_page,
)


async def test_authentication_retains_cookie(client, cdash_api):
    await client.get_build_summary(1)
    await client.get_build_summary(2)
    _, requests = cdash_api
    assert sum(r.method == "GET" for r in requests) == 1
    graphql = [r for r in requests if r.method == "POST"]
    assert all("cdash_session=valid" in r.headers["cookie"] for r in graphql)
    assert "secret-token" not in repr(client)


async def test_expired_session_reauthenticates(client, cdash_api):
    await client.build(1)
    original, requests = cdash_api
    failed = False

    def handle(request):
        nonlocal failed
        if (
            request.method == "POST"
            and "build(id:" in json.loads(request.content)["query"]
            and not failed
        ):
            failed = True
            return httpx.Response(
                200, json={"errors": [{"message": "This action is unauthorized."}]}
            )
        return original(request)

    client._client._transport = httpx.MockTransport(handle)
    await client.build(2)
    assert sum(r.method == "GET" for r in requests) == 2


async def test_unrecognized_token_fails_closed(client):
    client._client._transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": {"me": None}} if r.method == "POST" else {})
    )
    with pytest.raises(CDashAuthError, match="full-access"):
        await client.build(1)


@pytest.mark.parametrize(
    "body,exception",
    [
        ({"errors": [{"message": "This action is unauthorized."}]}, CDashAuthError),
        ({"errors": [{"message": "Unknown field"}], "data": {"build": {}}}, CDashError),
        ({"data": None}, CDashError),
        ({"data": {"build": None}}, CDashNotFoundError),
    ],
)
async def test_graphql_errors_not_empty_results(client, body, exception):
    client.token = None
    client._client._transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    with pytest.raises(exception):
        await client.build(1)


@pytest.mark.parametrize(
    "status,exception",
    [
        (400, CDashError),
        (401, CDashAuthError),
        (403, CDashAuthError),
        (404, CDashNotFoundError),
        (429, CDashError),
        (500, CDashError),
        (302, CDashError),
    ],
)
async def test_http_errors(client, status, exception):
    client._client._transport = httpx.MockTransport(
        lambda r: httpx.Response(status, json={"error": "detail"})
    )
    with pytest.raises(exception, match="detail"):
        await client.get_dashboard("thor")


async def test_invalid_json(client):
    client._client._transport = httpx.MockTransport(
        lambda r: httpx.Response(200, text="<html>login</html>")
    )
    with pytest.raises(CDashError, match="invalid JSON"):
        await client.get_dashboard("thor")


async def test_transport_error(client):
    def handle(request):
        raise httpx.ReadTimeout("timeout", request=request)

    client._client._transport = httpx.MockTransport(handle)
    with pytest.raises(CDashConnectionError):
        await client.get_dashboard("thor")


async def test_cursor_pagination(client, cdash_api):
    first = await client.get_build_tests(1, limit=1)
    second = await client.get_build_tests(1, limit=1, after=first["page_info"]["endCursor"])
    assert first["items"][0]["id"] == "10"
    assert second["items"][0]["id"] == "11"
    _, requests = cdash_api
    payloads = [json.loads(r.content) for r in requests if r.method == "POST"]
    assert any(p["variables"].get("after") == "1" for p in payloads)


async def test_status_and_warning_filter(client):
    assert [t["name"] for t in (await client.get_build_tests(1, "failed"))["items"]] == ["fixed"]
    assert (await client.get_build_errors(1, warnings=True))["items"][0]["type"] == "WARNING"


async def test_exact_history_uses_supported_rest(client, cdash_api):
    await client.get_test_summary("thor", "exact", "2026-10-02")
    request = cdash_api[1][-1]
    assert request.url.path.endswith("queryTests.php")
    assert request.url.params["compare1"] == "61"
    assert request.url.params["value1"] == "exact"
    assert "status" not in request.url.params.values()


async def test_stalled_pagination_fails(client, cdash_api):
    client.token = None
    conn = {"edges": [], "pageInfo": {"hasNextPage": True, "endCursor": None}}
    client._client._transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": {"build": {"tests": conn}}})
    )
    malformed = client._client._transport
    original, _ = cdash_api

    def handle(request):
        if "children(first:" in json.loads(request.content)["query"]:
            return original(request)
        return malformed.handle_request(request)

    client._client._transport = httpx.MockTransport(handle)
    with pytest.raises(CDashError, match="advance"):
        await client.get_build_tests(1, limit=1)
    with pytest.raises(CDashError, match="advance"):
        await client.all_items("build", 1, "tests", "id")


@pytest.mark.parametrize("value", ["2026-2-01", "2026-02-30", "nonsense", "20261002"])
def test_invalid_date(value):
    with pytest.raises(CDashError):
        validate_date(value)


@pytest.mark.parametrize("limit,offset", [(0, 0), (201, 0), (10, -1)])
def test_invalid_page(limit, offset):
    with pytest.raises(CDashError):
        validate_page(limit, offset)


async def test_invalid_status_and_cursor(client):
    with pytest.raises(CDashError):
        await client.get_build_tests(1, "Not Run")
    with pytest.raises(CDashError):
        await client.get_build_tests(1, after="not-a-cursor")


async def test_public_graphql_needs_no_session(client, cdash_api):
    client.token = None
    await client.build(1)
    assert all(request.method == "POST" for request in cdash_api[1])


async def test_malformed_connection_is_error(client, cdash_api):
    client.token = None
    client._client._transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "data": {
                    "build": {
                        "tests": {
                            "edges": [{"node": None}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            },
        )
    )
    malformed = client._client._transport
    original, _ = cdash_api

    def handle(request):
        if "children(first:" in json.loads(request.content)["query"]:
            return original(request)
        return malformed.handle_request(request)

    client._client._transport = httpx.MockTransport(handle)
    with pytest.raises(CDashError, match="connection records"):
        await client.get_build_tests(1)


async def test_changed_page_size_requires_restart(client):
    first = await client.get_build_tests(1, limit=1)
    with pytest.raises(CDashError, match="page size"):
        await client.get_build_tests(1, limit=2, after=first["page_info"]["endCursor"])


async def test_project_cursor_binds_page_size(client):
    first = await client.connection("project", "thor", "tests", "id", limit=1)
    with pytest.raises(CDashError, match="page size"):
        await client.connection(
            "project", "thor", "tests", "id", limit=2, after=first["page_info"]["endCursor"]
        )


async def test_child_catalog_uses_fixed_pages(client):
    client.token = None
    calls = []
    children = [{"id": str(i), "subProject": {"id": str(i), "name": str(i)}} for i in range(2, 203)]

    def handle(request):
        variables = json.loads(request.content)["variables"]
        calls.append(variables)
        first = variables["first"]
        start = int(variables.get("after") or 0) // first * first
        end = start + first
        return httpx.Response(
            200,
            json={
                "data": {
                    "build": {
                        "id": "1",
                        "subProject": None,
                        "children": {
                            "edges": [{"node": child} for child in children[start:end]],
                            "pageInfo": {
                                "hasNextPage": end < len(children),
                                "endCursor": str(min(end, len(children))),
                            },
                        },
                    }
                }
            },
        )

    client._client._transport = httpx.MockTransport(handle)
    sources = await client._build_sources(1)
    assert len(sources) == 202
    assert [call["first"] for call in calls] == [200, 200]
    assert [call["after"] for call in calls] == [None, "200"]
