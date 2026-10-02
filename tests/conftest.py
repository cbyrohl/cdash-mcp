"""Deterministic HTTP fixtures for modern CDash. [AI-Codex]"""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from cdash_mcp.client import CDashClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def cdash_api():
    requests = []
    tests = [
        {"id": "10", "name": "fixed", "status": "FAILED", "runningTime": 1},
        {"id": "11", "name": "new", "status": "PASSED", "runningTime": 2},
        {"id": "12", "name": "unchanged", "status": "PASSED", "runningTime": 3},
    ]
    coverage = [
        {
            "id": "20",
            "filePath": "src/a.cpp",
            "linesOfCodeTested": 1,
            "linesOfCodeUntested": 3,
            "linePercentage": 25,
            "branchesTested": 1,
            "branchesUntested": 0,
            "functionsTested": 1,
            "functionsUntested": 0,
            "branchPercentage": 100,
            "functionPercentage": 100,
            "file": "source text",
            "coveredLines": [{"lineNumber": 1, "timesHit": 1}],
        }
    ]

    def handle(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("index.php"):
            return httpx.Response(
                200,
                json={
                    "version": "v5.4.0",
                    "date": "2026-10-02",
                    "buildgroups": [{"name": "Nightly", "builds": [{"id": 1}]}],
                },
                headers={"set-cookie": "cdash_session=valid; Path=/"},
            )
        if path.endswith("build.php"):
            return httpx.Response(
                200, json={}, headers={"set-cookie": "cdash_session=valid; Path=/"}
            )
        if path.endswith("overview.php"):
            return httpx.Response(200, json={"projectname": "thor", "groups": []})
        if path.endswith("queryTests.php"):
            return httpx.Response(
                200,
                json={
                    "builds": [
                        {
                            "testname": "fixed",
                            "status": "Failed",
                            "buildSummaryLink": "builds/1",
                            "testDetailsLink": "tests/10",
                        }
                    ]
                },
            )
        assert path.endswith("graphql"), path
        payload = json.loads(request.content)
        query, variables = payload["query"], payload["variables"]
        if "me {" in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "me": {"id": "42"}
                        if "cdash_session=valid" in request.headers.get("cookie", "")
                        else None,
                        "__type": {"fields": [{"name": "tests"}, {"name": "coverage"}]},
                    }
                },
            )
        if "project(name:$project) { id name }" in query:
            return httpx.Response(200, json={"data": {"project": {"id": "100", "name": "thor"}}})
        if "test(id:$id)" in query and "testImages(first:" not in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "test": {
                            "id": variables["id"],
                            "name": "fixed",
                            "output": "0123456789",
                            "status": "FAILED",
                            "testMeasurements": [],
                            "command": "thor",
                        }
                    }
                },
            )
        if "dynamicAnalysis(id:$id)" in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "dynamicAnalysis": {
                            "id": variables["id"],
                            "log": "0123456789",
                            "defects": [],
                        }
                    }
                },
            )
        parent = (
            "project" if "project(name:" in query else ("test" if "test(id:" in query else "build")
        )
        rows = None
        relation = None
        for candidate in [
            "buildErrors",
            "tests",
            "coverage",
            "dynamicAnalyses",
            "notes",
            "files",
            "urls",
            "commands",
            "builds",
            "testImages",
            "updateFiles",
        ]:
            if candidate + "(first:" in query:
                relation = candidate
                break
        if relation == "tests":
            rows = copy.deepcopy(tests)
            if variables.get("id") == "2":
                rows[0]["status"] = "PASSED"
                rows[1]["status"] = "FAILED"
            status = variables.get("filters", {}).get("eq", {}).get("status")
            if status:
                rows = [r for r in rows if r["status"] == status]
            for r in rows:
                r["build"] = {"id": "1", "name": "ci"}
        elif relation == "buildErrors":
            kind = variables["filters"]["eq"]["type"]
            rows = [
                {
                    "id": "30",
                    "type": kind,
                    "sourceFile": "a.cpp",
                    "sourceLine": 2,
                    "stdError": "x" * 1000,
                    "stdOutput": "context",
                    "command": "clang",
                }
            ]
        elif relation == "coverage":
            rows = copy.deepcopy(coverage)
            if variables.get("id") == "2":
                rows[0].update(linesOfCodeTested=2, linesOfCodeUntested=2, linePercentage=50)
                rows.append(
                    {
                        **rows[0],
                        "id": "21",
                        "filePath": "src/b.cpp",
                        "linesOfCodeTested": 90,
                        "linesOfCodeUntested": 10,
                        "linePercentage": 90,
                    }
                )
        elif relation == "builds":
            rows = [{"id": "1", "name": "ci"}]
        elif relation == "notes":
            rows = [{"id": "1", "name": "environment", "text": "0123456789"}]
        elif relation == "files":
            rows = [{"id": "1", "name": "plot.png", "size": 100, "sha1sum": "abc"}]
        elif relation == "urls":
            rows = [{"id": "1", "href": "https://example.test/plot"}]
        elif relation == "testImages":
            rows = [{"id": "1", "role": "comparison", "url": "/images/1"}]
        elif relation in {"commands", "dynamicAnalyses", "updateFiles"}:
            rows = []
        if rows is not None:
            start = int(variables.get("after") or "0")
            end = start + variables["first"]
            conn = {
                "edges": [{"node": r} for r in rows[start:end]],
                "pageInfo": {"hasNextPage": end < len(rows), "endCursor": str(min(end, len(rows)))},
            }
            root = {relation: conn}
            if relation == "updateFiles":
                root = {"updateStep": {relation: conn}}
            return httpx.Response(200, json={"data": {parent: root}})
        return httpx.Response(
            200,
            json={
                "data": {
                    "build": {
                        "id": variables["id"],
                        "name": "ci",
                        "project": {"id": "100", "name": "thor"},
                        "buildErrorsCount": 1,
                        "buildWarningsCount": 2,
                        "configure": {"command": "cmake", "log": "0123456789", "returnValue": 0},
                        "updateStep": {"revision": "abc", "priorRevision": "def"},
                    }
                }
            },
        )

    return handle, requests


@pytest_asyncio.fixture
async def client(cdash_api):
    handler, _ = cdash_api
    async with CDashClient(base_url="https://cdash.test", token="secret-token") as client:
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url="https://cdash.test/",
            headers={"Authorization": "Bearer secret-token"},
            transport=httpx.MockTransport(handler),
        )
        yield client


@pytest.fixture
def ctx(client):
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"client": client}))
