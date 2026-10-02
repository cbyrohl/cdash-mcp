"""Read-only CDash REST and GraphQL client. [AI-Codex]"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import date as date_type
from typing import Any

import httpx


class CDashError(Exception):
    """CDash request or response failed."""


class CDashAuthError(CDashError):
    """Authentication or project access was denied."""


class CDashNotFoundError(CDashError):
    """Requested data or endpoint does not exist."""


class CDashConnectionError(CDashError):
    """CDash could not be reached."""


SUMMARY_FIELDS = """
    id name startTime endTime submissionTime buildType stamp generator command
    configureErrorsCount configureWarningsCount configureDuration
    buildErrorsCount buildWarningsCount buildDuration
    passedTestsCount failedTestsCount notRunTestsCount testDuration
    site { id name } project { id name } compilerName compilerVersion
    updateStep { revision priorRevision }
"""
TEST_FIELDS = """
    id name status runningTime details timeStatusCategory
    meanRunningTime stdDevRunningTime
"""
COVERAGE_FIELDS = """
    id filePath linesOfCodeTested linesOfCodeUntested linePercentage
    branchesTested branchesUntested branchPercentage
    functionsTested functionsUntested functionPercentage
"""
STATUS = {"passed": "PASSED", "failed": "FAILED", "notrun": "NOT_RUN"}


def validate_date(value: str | None) -> None:
    if value is not None:
        try:
            if date_type.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError as exc:
            raise CDashError("Date must use YYYY-MM-DD format.") from exc


def validate_page(limit: int, offset: int = 0) -> None:
    if not 1 <= limit <= 200 or offset < 0:
        raise CDashError("limit must be 1–200 and offset must be nonnegative.")


@dataclass
class CDashClient:
    """One HTTP session per MCP lifespan; credentials never appear in repr."""

    base_url: str = field(default_factory=lambda: os.getenv("CDASH_URL", "https://my.cdash.org"))
    token: str | None = field(default_factory=lambda: os.getenv("CDASH_TOKEN"), repr=False)
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _authenticated: bool = field(default=False, init=False, repr=False)
    _auth_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def __aenter__(self) -> CDashClient:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url.rstrip("/") + "/",
            headers=headers,
            timeout=30,
            follow_redirects=False,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._authenticated = False

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self._client is None:
            raise CDashError("Client not initialized; use async with CDashClient().")
        try:
            return await self._client.request(method, path.lstrip("/"), **kwargs)
        except httpx.RequestError as exc:
            raise CDashConnectionError(
                f"Request to {self.base_url} failed: {type(exc).__name__}"
            ) from exc

    @staticmethod
    def _decode(resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.is_error or resp.is_redirect:
            detail = str(body.get("error", ""))[:500] if isinstance(body, dict) else ""
            message = f"CDash HTTP {resp.status_code}: {detail or 'request failed'}"
            if resp.status_code in (401, 403):
                raise CDashAuthError(message)
            if resp.status_code == 404:
                raise CDashNotFoundError(message)
            raise CDashError(message)
        if not isinstance(body, dict):
            raise CDashError("CDash returned an invalid JSON object.")
        if body.get("error"):
            raise CDashError(str(body["error"]))
        return body

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._decode(await self._request("GET", path, params=params))

    async def _graphql_raw(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        body = self._decode(
            await self._request(
                "POST",
                "graphql",
                json={
                    "query": query,
                    "variables": variables,
                },
            )
        )
        if body.get("errors"):
            messages = "; ".join(str(e.get("message", "GraphQL error")) for e in body["errors"])
            if any(
                word in messages.lower()
                for word in ("unauthorized", "unauthenticated", "forbidden")
            ):
                raise CDashAuthError(messages)
            raise CDashError(f"GraphQL: {messages}")
        if not isinstance(body.get("data"), dict):
            raise CDashError("GraphQL response has no data object.")
        return body["data"]

    async def _authenticate(self, variables: dict[str, Any]) -> None:
        if not self.token:
            return
        async with self._auth_lock:
            if self._authenticated:
                return
            # CDash's REST middleware logs in bearer tokens and issues a session
            # cookie. GraphQL currently recognizes that cookie, not the bearer.
            if "id" in variables:
                path, params = "api/v1/build.php", {"buildid": variables["id"]}
            else:
                path, params = (
                    "api/v1/index.php",
                    {"project": variables.get("project", "PublicDashboard")},
                )
            resp = await self._request("GET", path, params=params)
            if resp.status_code not in (200, 400, 404):
                self._decode(resp)
            data = await self._graphql_raw("{ me { id } }", {})
            if data.get("me") is None:
                raise CDashAuthError(
                    "Token did not authenticate. Check expiry and full-access scope."
                )
            self._authenticated = True

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        variables = variables or {}
        if "id" in variables and (
            not str(variables["id"]).isdecimal() or int(variables["id"]) <= 0
        ):
            raise CDashError("ID must be a positive integer.")
        await self._authenticate(variables)
        try:
            return await self._graphql_raw(query, variables)
        except CDashAuthError:
            if not self.token:
                raise
            # Retry authentication once after a lost/expired server session.
            self._authenticated = False
            await self._authenticate(variables)
            return await self._graphql_raw(query, variables)

    async def build(self, build_id: int, fields: str = SUMMARY_FIELDS) -> dict[str, Any]:
        if build_id <= 0:
            raise CDashError("build_id must be positive.")
        data = await self.graphql(
            f"query($id:ID!) {{ build(id:$id) {{ {fields} }} }}", {"id": str(build_id)}
        )
        if data.get("build") is None:
            raise CDashNotFoundError(f"Build {build_id} was not found or is not accessible.")
        return data["build"]

    async def connection(
        self,
        parent: str,
        identifier: str | int,
        relation: str,
        fields: str,
        limit: int = 50,
        offset: int = 0,
        after: str | None = None,
        filters: dict[str, Any] | None = None,
        order: str = "ASC",
    ) -> dict[str, Any]:
        validate_page(limit, offset)
        if after and offset:
            raise CDashError("Use after or offset, not both.")
        if parent in {"build", "test"} and int(identifier) <= 0:
            raise CDashError("ID must be a positive integer.")
        parent_type = parent.capitalize()
        filter_type = f"{parent_type}{relation[0].upper() + relation[1:]}FiltersMultiFilterInput"
        filter_decl = f",$filters:{filter_type}" if filters else ""
        filter_arg = ",filters:$filters" if filters else ""
        # Only internal callers supply field/type names; user values are variables.
        key, scalar = ("id", "ID!") if parent in {"build", "test"} else ("project", "String!")
        selector = "id:$id" if parent in {"build", "test"} else "name:$project"
        ordered = relation in {"tests", "buildErrors", "coverage", "builds", "commands", "targets"}
        order_arg = f",orderBy:[{{column:ID,order:{order}}}]" if ordered else ""
        query = f"""query(${key}:{scalar},$first:Int!,$after:String{filter_decl}) {{
            {parent}({selector}) {{
                {relation}(first:$first,after:$after{filter_arg}{order_arg}) {{
                    edges {{ node {{ {fields} }} }} pageInfo {{ hasNextPage endCursor }}
                }}
            }}
        }}"""
        cursor = after
        remaining = offset
        while True:
            size = min(remaining, 200) if remaining else limit
            variables = {key: str(identifier), "first": size, "after": cursor}
            if filters:
                variables["filters"] = filters
            data = await self.graphql(query, variables)
            root = data.get(parent)
            if root is None:
                raise CDashNotFoundError(
                    f"{parent_type} {identifier} was not found or is inaccessible."
                )
            conn = root.get(relation)
            if not isinstance(conn, dict) or not isinstance(conn.get("edges"), list):
                raise CDashError(f"Invalid {relation} connection response.")
            page = conn.get("pageInfo")
            if not isinstance(page, dict):
                raise CDashError("Missing GraphQL pagination information.")
            if any(
                not isinstance(edge, dict) or not isinstance(edge.get("node"), dict)
                for edge in conn["edges"]
            ):
                raise CDashError("Invalid GraphQL connection records.")
            items = [edge["node"] for edge in conn["edges"]]
            if not remaining:
                return {"items": items, "page_info": page}
            remaining -= len(items)
            if not page.get("hasNextPage"):
                return {"items": [], "page_info": {"hasNextPage": False, "endCursor": None}}
            next_cursor = page.get("endCursor")
            if not items or not next_cursor or next_cursor == cursor:
                raise CDashError("CDash pagination did not advance.")
            cursor = next_cursor

    async def all_items(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor = None
        while True:
            page = await self.connection(*args, **kwargs, limit=200, after=cursor)
            items.extend(page["items"])
            if not page["page_info"].get("hasNextPage"):
                return items
            next_cursor = page["page_info"].get("endCursor")
            if not next_cursor or next_cursor == cursor or len(items) >= 10000:
                raise CDashError(
                    "Result exceeds the comparison limit or pagination did not advance."
                )
            cursor = next_cursor

    async def get_dashboard(self, project: str, date: str | None = None) -> dict[str, Any]:
        validate_date(date)
        return await self._get(
            "api/v1/index.php", {"project": project, **({"date": date} if date else {})}
        )

    async def query_tests(
        self,
        project: str,
        date: str | None = None,
        test_name: str | None = None,
        status_filter: str = "not_passed",
        exact: bool = False,
    ) -> dict[str, Any]:
        validate_date(date)
        if status_filter not in {"not_passed", "all", *STATUS}:
            raise CDashError("Invalid status filter.")
        params: dict[str, Any] = {"project": project, "filtercombine": "and"}
        if date:
            params["date"] = date
        count = 0
        for name, compare, value in [
            (
                "status",
                "62" if status_filter == "not_passed" else "61",
                "passed" if status_filter == "not_passed" else status_filter,
            ),
            ("testname", "61" if exact else "63", test_name),
        ]:
            if value is None or (name == "status" and status_filter == "all"):
                continue
            count += 1
            params.update(
                {f"field{count}": name, f"compare{count}": compare, f"value{count}": value}
            )
        params.update({"filtercount": count, "showfilters": "1"})
        return await self._get("api/v1/queryTests.php", params)

    async def get_build_summary(self, build_id: int) -> dict[str, Any]:
        return await self.build(build_id)

    async def get_build_errors(
        self,
        build_id: int,
        warnings: bool = False,
        limit: int = 30,
        offset: int = 0,
        after: str | None = None,
    ) -> dict[str, Any]:
        return await self.connection(
            "build",
            build_id,
            "buildErrors",
            """
            id type sourceFile sourceLine stdError stdOutput command
            targetName language workingDirectory exitCondition
        """,
            limit,
            offset,
            after,
            {"eq": {"type": "WARNING" if warnings else "ERROR"}},
        )

    async def get_build_tests(
        self,
        build_id: int,
        status_filter: str | None = None,
        limit: int = 50,
        offset: int = 0,
        after: str | None = None,
    ) -> dict[str, Any]:
        if status_filter is not None and status_filter not in STATUS:
            raise CDashError("status_filter must be passed, failed or notrun.")
        filters = {"eq": {"status": STATUS[status_filter]}} if status_filter else None
        return await self.connection(
            "build", build_id, "tests", TEST_FIELDS, limit, offset, after, filters
        )

    async def get_configure(self, build_id: int) -> dict[str, Any]:
        return await self.build(build_id, "id configure { command log returnValue }")

    async def get_test_details(self, test_id: int) -> dict[str, Any]:
        if test_id <= 0:
            raise CDashError("test_id must be positive.")
        data = await self.graphql(
            """query($id:ID!) { test(id:$id) {
            id name status runningTime details command output
            meanRunningTime stdDevRunningTime timeStatusCategory
            testMeasurements { id name type value }
            build { id name }
        }}""",
            {"id": str(test_id)},
        )
        if data.get("test") is None:
            raise CDashNotFoundError(f"Test {test_id} was not found or is inaccessible.")
        return data["test"]

    async def get_test_summary(
        self, project: str, test_name: str, date: str | None = None
    ) -> dict[str, Any]:
        return await self.query_tests(project, date, test_name, "all", exact=True)

    async def get_build_update(self, build_id: int) -> dict[str, Any]:
        return await self.build(
            build_id,
            """id updateStep { command type status revision priorRevision }
        """,
        )

    async def get_project_overview(self, project: str, date: str | None = None) -> dict[str, Any]:
        validate_date(date)
        return await self._get(
            "api/v1/overview.php", {"project": project, **({"date": date} if date else {})}
        )

    async def get_dynamic_analysis(
        self, build_id: int, limit: int = 50, offset: int = 0, after: str | None = None
    ) -> dict[str, Any]:
        return await self.connection(
            "build",
            build_id,
            "dynamicAnalyses",
            "id name status checker fullCommandLine defects { type value }",
            limit,
            offset,
            after,
        )
