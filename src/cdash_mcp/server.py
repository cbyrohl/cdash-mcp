"""Read-only MCP tools for current CDash APIs. [AI-Codex]"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date as date_type
from datetime import timedelta
from functools import wraps
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .client import (
    COVERAGE_FIELDS,
    SUMMARY_FIELDS,
    TEST_FIELDS,
    CDashClient,
    CDashError,
    validate_date,
    validate_page,
)

logging.basicConfig(stream=sys.stderr, level=logging.INFO)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    async with CDashClient() as client:
        yield {"client": client}


mcp = FastMCP("cdash-mcp", lifespan=lifespan)


def _get_client(ctx: Context) -> CDashClient:
    return ctx.request_context.lifespan_context["client"]


def tool(fn):
    """Mark tools read-only and propagate API failures as MCP errors."""

    @wraps(fn)
    async def wrapped(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except CDashError as exc:
            raise ToolError(str(exc)) from exc

    return mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))(wrapped)


def _slice_output(value: str, offset: int, limit: int) -> dict[str, Any]:
    if offset < 0 or limit < 0:
        raise CDashError("Output offset and limit must be nonnegative; limit=0 means unlimited.")
    output = value[offset : offset + limit] if limit else value[offset:]
    return {
        "text": output,
        "total_characters": len(value),
        "offset": offset,
        "next_offset": offset + len(output) if offset + len(output) < len(value) else None,
    }


def _rest_tests(
    client: CDashClient, data: dict[str, Any], limit: int, offset: int
) -> dict[str, Any]:
    validate_page(limit, offset)
    rows = data.get("builds")
    if not isinstance(rows, list):
        raise CDashError("CDash test query returned no results list.")
    items = []
    for row in rows[offset : offset + limit]:
        item = dict(row)
        for link, key in [("buildSummaryLink", "build_id"), ("testDetailsLink", "test_id")]:
            match = re.search(r"/(\d+)(?:\?.*)?$", str(item.get(link, "")))
            if match:
                item[key] = int(match[1])
                item[key.replace("_id", "_url")] = (
                    client.base_url.rstrip("/") + "/" + item[link].lstrip("/")
                )
        items.append(item)
    return {
        "items": items,
        "total": len(rows),
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "pagination": "local slicing of REST query results",
    }


@tool
async def get_dashboard(project: str, date: str | None = None, ctx: Context = None) -> dict:
    """Dashboard build groups, counts and build IDs for a CDash calendar date."""
    data = await _get_client(ctx).get_dashboard(project, date)
    if "buildgroups" not in data:
        raise CDashError("Dashboard response is missing build groups; check project access.")
    return {
        "project": project,
        "date": data.get("date"),
        "version": data.get("version", "").strip(),
        "build_groups": data["buildgroups"],
    }


@tool
async def get_failing_tests(
    project: str,
    date: str | None = None,
    test_name: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: Context = None,
) -> dict:
    """Non-passing results for a CDash date, including build/test IDs and URLs."""
    validate_page(limit, offset)
    client = _get_client(ctx)
    return _rest_tests(client, await client.query_tests(project, date, test_name), limit, offset)


@tool
async def get_build_details(build_id: int, ctx: Context = None) -> dict:
    """Build configure, compile and test counts, durations, site and source revision."""
    return await _get_client(ctx).get_build_summary(build_id)


@tool
async def get_build_errors(
    build_id: int,
    warnings: bool = False,
    limit: int = 30,
    after: str | None = None,
    output_offset: int = 0,
    output_limit: int = 34816,
    ctx: Context = None,
) -> dict:
    """Compiler errors or warnings; paginate records with after and message text with
    output_offset.
    """
    _slice_output("", output_offset, output_limit)
    page = await _get_client(ctx).get_build_errors(build_id, warnings, limit, after)
    for item in page["items"]:
        item["stdError"] = _slice_output(item.get("stdError") or "", output_offset, output_limit)
        item["stdOutput"] = _slice_output(item.get("stdOutput") or "", output_offset, output_limit)
    return page


@tool
async def get_build_tests(
    build_id: int,
    status_filter: str | None = None,
    limit: int = 50,
    after: str | None = None,
    ctx: Context = None,
) -> dict:
    """Tests, stable IDs and timing statistics. Filter: passed, failed or notrun. Use after to
    paginate.
    """
    page = await _get_client(ctx).get_build_tests(build_id, status_filter, limit, after)
    for item in page["items"]:
        item["test_url"] = f"{_get_client(ctx).base_url.rstrip('/')}/tests/{item['id']}"
    return page


@tool
async def get_configure_output(
    build_id: int, output_offset: int = 0, output_limit: int = 34816, ctx: Context = None
) -> dict:
    """CMake command, return value and paginated configure log; output_limit=0 retrieves all
    text.
    """
    _slice_output("", output_offset, output_limit)
    data = await _get_client(ctx).get_configure(build_id)
    configure = data.get("configure")
    if configure is not None:
        configure["log"] = _slice_output(configure.get("log") or "", output_offset, output_limit)
    return data


@tool
async def get_test_details(
    build_test_id: int, output_offset: int = 0, output_limit: int = 34816, ctx: Context = None
) -> dict:
    """Test command, status, measurements and paginated output. build_test_id is the current
    test ID.
    """
    _slice_output("", output_offset, output_limit)
    data = await _get_client(ctx).get_test_details(build_test_id)
    data["output"] = _slice_output(data.get("output") or "", output_offset, output_limit)
    return data


@tool
async def get_test_summary(
    project: str,
    test_name: str,
    date: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: Context = None,
) -> dict:
    """Exact-name test results across builds on one CDash date. Use get_test_history for
    multiple dates.
    """
    validate_page(limit, offset)
    client = _get_client(ctx)
    data = await client.get_test_summary(project, test_name, date)
    result = _rest_tests(client, data, limit, offset)
    rows = data["builds"]
    result["status_counts"] = {
        s: sum(r.get("status") == s for r in rows) for s in ("Passed", "Failed", "Not Run")
    }
    return result


@tool
async def get_build_update(build_id: int, ctx: Context = None) -> dict:
    """Source revision, prior revision, update command and status."""
    return await _get_client(ctx).get_build_update(build_id)


@tool
async def get_update_files(
    build_id: int,
    limit: int = 50,
    after: str | None = None,
    ctx: Context = None,
) -> dict:
    """Changed source files, authors and commit messages; paginated update records."""
    return await _get_client(ctx).connection(
        "build",
        build_id,
        "updateFiles",
        "id fileName authorName log revision priorRevision status",
        limit=limit,
        after=after,
    )


@tool
async def get_project_overview(project: str, date: str | None = None, ctx: Context = None) -> dict:
    """Project overview: build groups, coverage and analysis statistics."""
    data = await _get_client(ctx).get_project_overview(project, date)
    keys = (
        "projectname",
        "date",
        "groups",
        "coverages",
        "dynamicanalyses",
        "staticanalyses",
        "measurements",
    )
    if "projectname" not in data:
        raise CDashError("Project overview unavailable; check project access.")
    return {key: data.get(key) for key in keys}


@tool
async def get_build_coverage(
    build_id: int,
    limit: int = 50,
    after: str | None = None,
    path: str | None = None,
    ctx: Context = None,
) -> dict:
    """File line, branch and function coverage; optional path substring and cursor pagination."""
    filters = {"contains": {"filePath": path}} if path else None
    return await _get_client(ctx).connection(
        "build", build_id, "coverage", COVERAGE_FIELDS, limit=limit, after=after, filters=filters
    )


@tool
async def get_coverage_file(
    build_id: int,
    path: str,
    output_offset: int = 0,
    output_limit: int = 34816,
    limit: int = 50,
    after: str | None = None,
    ctx: Context = None,
) -> dict:
    """Exact file's source and covered-line hit counts, if CDash permits source access."""
    _slice_output("", output_offset, output_limit)
    result = await _get_client(ctx).connection(
        "build",
        build_id,
        "coverage",
        COVERAGE_FIELDS + " file coveredLines { lineNumber timesHit totalBranches branchesHit }",
        limit=limit,
        after=after,
        filters={"eq": {"filePath": path}},
    )
    for item in result["items"]:
        if item.get("file") is not None:
            item["file"] = _slice_output(item["file"], output_offset, output_limit)
    return result


@tool
async def compare_build_coverage(
    base_build_id: int, compare_build_id: int, limit: int = 50, offset: int = 0, ctx: Context = None
) -> dict:
    """Compare per-file coverage for two explicit builds; include added/removed files and
    weighted totals.
    """
    validate_page(limit, offset)
    client = _get_client(ctx)
    base_meta = await client.build(base_build_id, "id project { id name }")
    compare_meta = await client.build(compare_build_id, "id project { id name }")
    if base_meta["project"]["id"] != compare_meta["project"]["id"]:
        raise CDashError("Coverage comparisons require builds from the same project.")
    base = await client.all_items("build", base_build_id, "coverage", COVERAGE_FIELDS)
    compare = await client.all_items("build", compare_build_id, "coverage", COVERAGE_FIELDS)

    def totals(rows):
        tested = sum(r["linesOfCodeTested"] for r in rows)
        untested = sum(r["linesOfCodeUntested"] for r in rows)
        return {
            "tested": tested,
            "untested": untested,
            "percent": 100 * tested / (tested + untested) if tested + untested else None,
        }

    def index(rows):
        result = {}
        for row in rows:
            path = row.get("filePath")
            if not isinstance(path, str) or not path.strip():
                raise CDashError(
                    f"Coverage record {row.get('id', '?')} in build "
                    f"{row.get('build_id', '?')} has no usable file path; cannot compare coverage."
                )
            key = ((row.get("subproject") or {}).get("id", ""), path)
            if key in result:
                raise CDashError("Duplicate coverage files within a subproject prevent comparison.")
            result[key] = row
        return result

    a, b = index(base), index(compare)
    changes = []
    metrics = (
        "linesOfCodeTested",
        "linesOfCodeUntested",
        "branchesTested",
        "branchesUntested",
        "functionsTested",
        "functionsUntested",
    )
    for path in sorted(a.keys() | b.keys()):
        old, new = a.get(path), b.get(path)
        if old is None or new is None or any(old[k] != new[k] for k in metrics):
            changes.append(
                {
                    "path": path[1],
                    "subproject": (new or old).get("subproject"),
                    "base": old,
                    "compare": new,
                    "percentage_point_change": new["linePercentage"] - old["linePercentage"]
                    if old is not None and new is not None
                    else None,
                }
            )
    return {
        "base_build_id": base_build_id,
        "compare_build_id": compare_build_id,
        "base_totals": totals(base),
        "compare_totals": totals(compare),
        "changed_files": changes[offset : offset + limit],
        "total_changed_files": len(changes),
        "next_offset": offset + limit if offset + limit < len(changes) else None,
        "note": "Per-file comparison; this does not compute patch coverage.",
    }


@tool
async def get_coverage_comparison(
    project: str,
    date: str | None = None,
    build_id: int | None = None,
    limit: int = 50,
    after: str | None = None,
    compare_build_id: int | None = None,
    ctx: Context = None,
) -> dict:
    """Compatibility alias. Supply build_id for coverage, plus compare_build_id for comparison."""
    validate_date(date)
    validate_page(limit)
    if build_id is None:
        raise CDashError(
            "Supply build_id; use search_builds to choose builds. "
            "Prefer get_build_coverage or compare_build_coverage."
        )
    client = _get_client(ctx)
    build = await client.build(build_id, "id project { name }")
    if build["project"]["name"] != project:
        raise CDashError("build_id belongs to a different project.")
    if compare_build_id is not None:
        if after is not None:
            raise CDashError("Use compare_build_coverage to paginate comparison results.")
        return await compare_build_coverage(build_id, compare_build_id, limit, ctx=ctx)
    return await client.connection("build", build_id, "coverage", COVERAGE_FIELDS, limit, after)


@tool
async def get_dynamic_analysis(
    build_id: int, limit: int = 50, after: str | None = None, ctx: Context = None
) -> dict:
    """Memory/sanitizer check results with checker, command and per-type defect counts."""
    return await _get_client(ctx).get_dynamic_analysis(build_id, limit, after)


@tool
async def get_dynamic_analysis_details(
    analysis_id: int, output_offset: int = 0, output_limit: int = 34816, ctx: Context = None
) -> dict:
    """Paginated log and defect details for a dynamic-analysis result."""
    _slice_output("", output_offset, output_limit)
    data = await _get_client(ctx).graphql(
        """query($id:ID!) { dynamicAnalysis(id:$id) {
        id name checker status fullCommandLine log defects { type value }
    }}""",
        {"id": str(analysis_id)},
    )
    result = data.get("dynamicAnalysis")
    if result is None:
        raise CDashError("Dynamic-analysis record was not found or is inaccessible.")
    result["log"] = _slice_output(result.get("log") or "", output_offset, output_limit)
    return result


@tool
async def check_connection(project: str | None = None, ctx: Context = None) -> dict:
    """Check GraphQL authentication, project access, server version and available build fields."""
    client = _get_client(ctx)
    variables = {"project": project} if project else {}
    data = await client.graphql('{ me { id } __type(name:"Build") { fields { name } } }', variables)
    result = {
        "base_url": client.base_url,
        "authenticated": data.get("me") is not None,
        "user_id": (data.get("me") or {}).get("id"),
        "graphql": True,
        "build_fields": [f["name"] for f in (data.get("__type") or {}).get("fields", [])],
    }
    if project:
        access = await client.graphql(
            "query($project:String!) { project(name:$project) { id name } }", variables
        )
        if access.get("project") is None:
            raise CDashError("Project was not found or is inaccessible.")
        result["project"] = access["project"]
        result["version"] = (await client.get_dashboard(project)).get("version", "").strip()
    return result


def _date_filters(start_date, end_date):
    validate_date(start_date)
    validate_date(end_date)
    if start_date and end_date and start_date > end_date:
        raise CDashError("start_date must be on or before end_date.")
    filters = []
    if start_date:
        filters.append({"ge": {"startTime": start_date + "T00:00:00Z"}})
    if end_date:
        try:
            next_day = date_type.fromisoformat(end_date) + timedelta(days=1)
        except OverflowError as exc:
            raise CDashError("end_date must be before 9999-12-31.") from exc
        filters.append({"lt": {"startTime": next_day.isoformat() + "T00:00:00Z"}})
    return filters


@tool
async def search_builds(
    project: str,
    name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    build_type: str | None = None,
    revision: str | None = None,
    limit: int = 50,
    after: str | None = None,
    ctx: Context = None,
) -> dict:
    """Search build history by name substring, inclusive UTC dates, type or exact source
    revision.
    """
    filters = _date_filters(start_date, end_date)
    if name:
        filters.append({"contains": {"name": name}})
    if build_type:
        filters.append({"eq": {"buildType": build_type}})
    if revision:
        filters.append({"has": {"updateStep": {"eq": {"revision": revision}}}})
    return await _get_client(ctx).connection(
        "project",
        project,
        "builds",
        SUMMARY_FIELDS,
        limit=limit,
        after=after,
        filters={"all": filters} if filters else None,
        order="DESC",
    )


@tool
async def get_test_history(
    project: str,
    test_name: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 50,
    after: str | None = None,
    ctx: Context = None,
) -> dict:
    """Exact-name test history with build IDs and timing; optional inclusive UTC build-date
    range.
    """
    filters = [{"eq": {"name": test_name}}]
    dates = _date_filters(start_date, end_date)
    if dates:
        filters.append({"has": {"build": {"all": dates}}})
    return await _get_client(ctx).connection(
        "project",
        project,
        "tests",
        TEST_FIELDS + " build { id name startTime updateStep { revision } }",
        limit=limit,
        after=after,
        filters={"all": filters},
        order="DESC",
    )


@tool
async def compare_builds(
    base_build_id: int, compare_build_id: int, limit: int = 50, offset: int = 0, ctx: Context = None
) -> dict:
    """Compare build counts and new/fixed test failures by exact test name; show runtime changes."""
    validate_page(limit, offset)
    client = _get_client(ctx)
    base = await client.build(base_build_id)
    compare = await client.build(compare_build_id)
    if base["project"]["id"] != compare["project"]["id"]:
        raise CDashError("Build comparisons require builds from the same project.")
    old = await client.all_items("build", base_build_id, "tests", TEST_FIELDS)
    new = await client.all_items("build", compare_build_id, "tests", TEST_FIELDS)

    def index(rows):
        result = {}
        for row in rows:
            key = ((row.get("subproject") or {}).get("id", ""), row["name"])
            if key in result:
                raise CDashError("Duplicate test names within a subproject prevent comparison.")
            result[key] = row
        return result

    a, b = index(old), index(new)
    changes = []
    for name in sorted(a.keys() | b.keys()):
        before, now = a.get(name), b.get(name)
        if (
            before is None
            or now is None
            or before["status"] != now["status"]
            or before["runningTime"] != now["runningTime"]
        ):
            changes.append(
                {
                    "name": name[1],
                    "subproject": (now or before).get("subproject"),
                    "base": before,
                    "compare": now,
                    "new_failure": now is not None
                    and now["status"] == "FAILED"
                    and (before is None or before["status"] != "FAILED"),
                    "fixed_failure": before is not None
                    and before["status"] == "FAILED"
                    and now is not None
                    and now["status"] == "PASSED",
                }
            )
    return {
        "base": base,
        "compare": compare,
        "total_test_changes": len(changes),
        "new_failures": [r["name"] for r in changes if r["new_failure"]],
        "fixed_failures": [r["name"] for r in changes if r["fixed_failure"]],
        "new_failure_results": [r for r in changes if r["new_failure"]],
        "fixed_failure_results": [r for r in changes if r["fixed_failure"]],
        "test_changes": changes[offset : offset + limit],
        "next_offset": offset + limit if offset + limit < len(changes) else None,
    }


@tool
async def get_build_triage(build_id: int, limit: int = 10, ctx: Context = None) -> dict:
    """One-call build summary, configure log, compiler errors and failed tests; bounded first
    pages.
    """
    validate_page(limit)
    client = _get_client(ctx)
    return {
        "summary": await client.build(build_id),
        "configure": await get_configure_output(build_id, ctx=ctx),
        "compiler_errors": await get_build_errors(build_id, limit=limit, ctx=ctx),
        "failed_tests": await get_build_tests(build_id, "failed", limit=limit, ctx=ctx),
    }


@tool
async def get_build_notes(
    build_id: int,
    limit: int = 50,
    after: str | None = None,
    output_offset: int = 0,
    output_limit: int = 34816,
    ctx: Context = None,
) -> dict:
    """Build notes with paginated text (compiler environment, configuration or submitted
    diagnostics).
    """
    _slice_output("", output_offset, output_limit)
    page = await _get_client(ctx).connection(
        "build", build_id, "notes", "id name text", limit=limit, after=after
    )
    for item in page["items"]:
        item["text"] = _slice_output(item.get("text") or "", output_offset, output_limit)
    return page


@tool
async def get_build_artifacts(
    build_id: int,
    limit: int = 50,
    after: str | None = None,
    kind: str = "files",
    ctx: Context = None,
) -> dict:
    """List uploaded files/download URLs or submitted links. kind is files or urls."""
    if kind not in {"files", "urls"}:
        raise CDashError("kind must be files or urls.")
    client = _get_client(ctx)
    fields = "id name size sha1sum" if kind == "files" else "id href"
    page = await client.connection("build", build_id, kind, fields, limit=limit, after=after)
    if kind == "files":
        for item in page["items"]:
            item["download_url"] = (
                f"{client.base_url.rstrip('/')}/builds/{build_id}/files/{item['id']}"
            )
    return page


@tool
async def get_build_commands(
    build_id: int, limit: int = 50, after: str | None = None, ctx: Context = None
) -> dict:
    """Submitted CMake instrumentation: command timings, results and resource measurements."""
    return await _get_client(ctx).connection(
        "build",
        build_id,
        "commands",
        """
        id type command startTime duration result workingDirectory source language config
        measurements(first:20) {
            edges { node { name type value } } pageInfo { hasNextPage endCursor }
        }
    """,
        limit=limit,
        after=after,
    )


@tool
async def get_test_images(
    build_test_id: int,
    limit: int = 50,
    after: str | None = None,
    ctx: Context = None,
) -> dict:
    """List submitted test images and their URLs, with cursor pagination."""
    return await _get_client(ctx).connection(
        "test",
        build_test_id,
        "testImages",
        "id role url",
        limit=limit,
        after=after,
    )


def main():
    mcp.run()
