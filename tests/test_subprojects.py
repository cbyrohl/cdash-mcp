"""Parent-build aggregation regressions for Copilot's review. [AI-Codex]"""

import json

import httpx
import pytest

from cdash_mcp import server as s
from cdash_mcp.client import CDashError


@pytest.fixture
async def subprojects(client):
    client.token = None
    calls = []
    children = {
        "100": [("11", "a"), ("12", "b"), ("13", "empty")],
        "200": [("21", "a"), ("22", "b"), ("23", "empty")],
    }

    def connection(rows, variables):
        start = int(variables.get("after") or 0)
        end = start + variables["first"]
        return {
            "edges": [{"node": r} for r in rows[start:end]],
            "pageInfo": {"hasNextPage": end < len(rows), "endCursor": str(min(end, len(rows)))},
        }

    def handle(request):
        payload = json.loads(request.content)
        query, variables = payload["query"], payload["variables"]
        calls.append(payload)
        build_id = variables["id"]
        if "children(first:" in query:
            kids = [
                {"id": i, "subProject": {"id": name, "name": name}}
                for i, name in children.get(build_id, [])
            ]
            # Limit catalog pages to one child to exercise independent child pagination.
            conn = connection(kids, {**variables, "first": 1})
            root = {"id": build_id, "subProject": None, "children": conn}
        elif "tests(first:" in query:
            if build_id in {"13", "23"}:
                rows = []
            else:
                status = "FAILED" if build_id == "11" else "PASSED"
                rows = [
                    {"id": build_id + "0", "name": "shared", "status": status, "runningTime": 1}
                ]
                if build_id == "11":
                    rows.append(
                        {"id": "111", "name": "other", "status": "PASSED", "runningTime": 2}
                    )
                if build_id == "21":
                    rows.append(
                        {"id": "211", "name": "other", "status": "PASSED", "runningTime": 2}
                    )
            status = variables.get("filters", {}).get("eq", {}).get("status")
            if status:
                rows = [r for r in rows if r["status"] == status]
            root = {"tests": connection(rows, variables)}
        elif "coverage(first:" in query:
            rows = (
                []
                if build_id in {"100", "200", "13", "23"}
                else [
                    {
                        "id": build_id,
                        "filePath": "same.cpp",
                        "linesOfCodeTested": 1 if build_id == "11" else 2,
                        "linesOfCodeUntested": 1 if build_id == "11" else 0,
                        "linePercentage": 50 if build_id == "11" else 100,
                        "branchesTested": 0,
                        "branchesUntested": 0,
                        "functionsTested": 0,
                        "functionsUntested": 0,
                    }
                ]
            )
            root = {"coverage": connection(rows, variables)}
        elif "buildErrors(first:" in query:
            rows = [] if build_id in {"100", "13"} else [{"id": build_id, "type": "ERROR"}]
            root = {"buildErrors": connection(rows, variables)}
        elif "commands(first:" in query:
            rows = [] if build_id in {"100", "13"} else [{"id": build_id, "command": "clang"}]
            root = {"commands": connection(rows, variables)}
        else:
            root = {"id": build_id, "name": "parent", "project": {"id": "p", "name": "project"}}
        return httpx.Response(200, json={"data": {"build": root}})

    client._client._transport = httpx.MockTransport(handle)
    return client, calls


async def test_parent_and_children_paginate_without_gaps(subprojects):
    client, calls = subprojects
    first = await client.get_build_tests(100, limit=2)
    second = await client.get_build_tests(100, limit=2, after=first["page_info"]["endCursor"])
    assert [r["id"] for r in first["items"] + second["items"]] == ["1000", "110", "111", "120"]
    assert first["items"][0]["build_id"] == "100"
    assert first["items"][1]["subproject"] == {"id": "a", "name": "a"}
    assert second["items"][1]["subproject"]["id"] == "b"
    # Exhaust empty children as well; no fabricated records.
    tail = await client.get_build_tests(100, after=second["page_info"]["endCursor"])
    assert tail["items"] == [] and not tail["page_info"]["hasNextPage"]
    offset = await client.get_build_tests(100, offset=2, limit=2)
    assert [r["id"] for r in offset["items"]] == ["111", "120"]
    assert len([c for c in calls if "children(first:" in c["query"]]) >= 3


async def test_filter_applies_to_every_child(subprojects):
    client, _ = subprojects
    result = await client.get_build_tests(100, "failed", limit=2)
    assert [(r["build_id"], r["name"]) for r in result["items"]] == [("11", "shared")]
    assert not result["page_info"]["hasNextPage"]


@pytest.mark.parametrize("relation", ["buildErrors", "coverage", "commands"])
async def test_other_child_relations_are_not_empty(subprojects, relation):
    client, _ = subprojects
    result = await client.all_items("build", 100, relation, "id")
    assert [r["build_id"] for r in result] == ["11", "12"]


async def test_cursor_cannot_be_reused_for_different_build_or_filter(subprojects):
    client, _ = subprojects
    page = await client.get_build_tests(100, limit=1)
    cursor = page["page_info"]["endCursor"]
    with pytest.raises(CDashError, match="cursor"):
        await client.get_build_tests(200, after=cursor)
    with pytest.raises(CDashError, match="cursor"):
        await client.get_build_tests(100, "failed", after=cursor)


async def test_comparisons_preserve_subprojects(subprojects, ctx):
    tests = await s.compare_builds(100, 200, ctx=ctx)
    assert tests["fixed_failures"] == ["shared"]
    assert tests["test_changes"][0]["subproject"]["id"] == "a"
    coverage = await s.compare_build_coverage(100, 200, ctx=ctx)
    assert coverage["total_changed_files"] == 1
    assert coverage["changed_files"][0]["subproject"]["id"] == "a"
    assert coverage["base_totals"]["tested"] == 3
