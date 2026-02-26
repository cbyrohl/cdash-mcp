"""FastMCP server exposing CDash CI/CD data as tools. [AI-Claude]"""

import logging
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import Context, FastMCP

from .client import CDashClient, CDashError

# Log to stderr only (STDIO transport uses stdout)
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("cdash-mcp")


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Share a single CDashClient across all tool invocations."""
    client = CDashClient()
    logger.info("CDash MCP server starting (base_url=%s)", client.base_url)
    async with client:
        yield {"client": client}


mcp = FastMCP("cdash-mcp", lifespan=lifespan)


def _get_client(ctx: Context) -> CDashClient:
    """Extract the CDashClient from the request context."""
    return ctx.request_context.lifespan_context["client"]


# ---------------------------------------------------------------------------
# Tool: get_dashboard
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_dashboard(
    project: str,
    date: str | None = None,
    ctx: Context = None,
) -> str:
    """Get the CDash dashboard for a project, showing build groups and status.

    Args:
        project: CDash project name (e.g. "PublicDashboard").
        date: Optional date (YYYY-MM-DD). Defaults to today.
    """
    client = _get_client(ctx)
    try:
        data = await client.get_dashboard(project, date)
    except CDashError as e:
        return f"Error: {e}"

    lines: list[str] = []
    title = data.get("title", project)
    dashboard_date = data.get("datetime", date or "today")
    lines.append(f"# {title} - Dashboard ({dashboard_date})")
    lines.append("")

    build_groups = data.get("buildgroups", [])
    if not build_groups:
        lines.append("No build groups found.")
        return "\n".join(lines)

    for group in build_groups:
        group_name = group.get("name", "Unknown")
        builds = group.get("builds", [])
        lines.append(f"## {group_name} ({len(builds)} builds)")
        lines.append("")

        # Show up to 20 builds with issues first, then summarize rest
        shown = 0
        for build in builds:
            name = build.get("buildname", "?")
            site = build.get("site", "?")
            configure_errors = build.get("configure", {}).get("error", 0)
            compile_errors = build.get("compilation", {}).get("error", 0)
            compile_warnings = build.get("compilation", {}).get("warning", 0)
            test_fail = build.get("test", {}).get("fail", 0)
            test_notrun = build.get("test", {}).get("notrun", 0)
            test_pass = build.get("test", {}).get("pass", 0)
            build_id = build.get("id", "?")

            has_issues = (
                configure_errors or compile_errors or test_fail or test_notrun
            )

            if has_issues or shown < 20:
                status_parts = []
                if configure_errors:
                    status_parts.append(f"configure_err={configure_errors}")
                if compile_errors:
                    status_parts.append(f"compile_err={compile_errors}")
                if compile_warnings:
                    status_parts.append(f"warnings={compile_warnings}")
                if test_fail:
                    status_parts.append(f"test_fail={test_fail}")
                if test_notrun:
                    status_parts.append(f"test_notrun={test_notrun}")
                if test_pass:
                    status_parts.append(f"test_pass={test_pass}")

                status = ", ".join(status_parts) if status_parts else "OK"
                marker = "!!!" if has_issues else ""
                lines.append(
                    f"- {marker}[id={build_id}] {name} @ {site}: {status}"
                )
                shown += 1

        if shown < len(builds):
            lines.append(f"  ... and {len(builds) - shown} more builds")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_failing_tests
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_failing_tests(
    project: str,
    date: str | None = None,
    test_name: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: Context = None,
) -> str:
    """Find non-passing tests across all builds for a project. Most useful for CI triage.

    Args:
        project: CDash project name (e.g. "PublicDashboard").
        date: Optional date (YYYY-MM-DD). Defaults to today.
        test_name: Optional filter to match test names containing this string.
        limit: Maximum number of tests to return (default 50, max 200).
        offset: Number of tests to skip (default 0). Use for pagination.
    """
    client = _get_client(ctx)
    try:
        data = await client.query_tests(project, date, test_name)
    except CDashError as e:
        return f"Error: {e}"

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    lines: list[str] = []
    lines.append(f"# Failing Tests for {project}")
    lines.append("")

    tests = data.get("builds", [])
    if not tests:
        lines.append("No failing tests found.")
        return "\n".join(lines)

    total = len(tests)
    page = tests[offset : offset + limit]

    if not page:
        lines.append(
            f"Found {total} non-passing test result(s)"
            f" — no results in this range (offset={offset})."
        )
        return "\n".join(lines)

    lines.append(
        f"Found {total} non-passing test result(s)"
        f" (showing {offset + 1}–{offset + len(page)}):"
    )
    lines.append("")

    for t in page:
        test_name_val = t.get("testname", "?")
        status = t.get("status", "?")
        build_name = t.get("buildName", "?")
        site = t.get("site", "?")
        details = t.get("details", "")
        build_id_val = t.get("buildid", "?")

        lines.append(f"- **{test_name_val}** [{status}]")
        lines.append(f"  Build: {build_name} @ {site} (build_id={build_id_val})")
        if details:
            # Truncate long details
            if len(details) > 200:
                details = details[:200] + "..."
            lines.append(f"  Details: {details}")
        lines.append("")

    remaining = total - offset - len(page)
    if remaining > 0:
        lines.append(f"... {remaining} more (use offset={offset + limit} to see next page)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_build_details
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_build_details(
    build_id: int,
    ctx: Context = None,
) -> str:
    """Get detailed information about a specific build, including configure/compile/test summaries.

    Args:
        build_id: The CDash build ID.
    """
    client = _get_client(ctx)
    try:
        data = await client.get_build_summary(build_id)
    except CDashError as e:
        return f"Error: {e}"

    lines: list[str] = []

    build = data.get("build", {})
    build_name = build.get("name", "?")
    site = build.get("site", "?")
    build_type = build.get("type", "?")
    start_time = build.get("starttime", "?")
    lines.append(f"# Build: {build_name}")
    lines.append(f"**Site**: {site}  ")
    lines.append(f"**Type**: {build_type}  ")
    lines.append(f"**Started**: {start_time}  ")
    lines.append(f"**Build ID**: {build_id}")
    lines.append("")

    # Configure summary
    configure = data.get("configure", {})
    if configure:
        conf_errors = configure.get("nerrors", 0)
        conf_warnings = configure.get("nwarnings", 0)
        conf_status = "PASS" if conf_errors == 0 else "FAIL"
        lines.append(
            f"## Configure: {conf_status} "
            f"({conf_errors} errors, {conf_warnings} warnings)"
        )
        lines.append("")

    # Test summary
    test = data.get("test", {})
    if test:
        test_pass = test.get("pass", 0)
        test_fail = test.get("fail", 0)
        test_notrun = test.get("notrun", 0)
        lines.append(
            f"## Tests: {test_pass} passed, {test_fail} failed, "
            f"{test_notrun} not run"
        )
        lines.append("")

    # Previous build comparison
    prev = data.get("previousbuild", {})
    if prev and prev.get("id"):
        prev_id = prev["id"]
        lines.append(f"## Previous build: id={prev_id}")
        lines.append("")

    # Update info
    update = data.get("update", {})
    if update:
        n_files = update.get("files", 0)
        if n_files:
            lines.append(f"## Source changes: {n_files} file(s) updated")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_build_errors
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_build_errors(
    build_id: int,
    warnings: bool = False,
    limit: int = 30,
    offset: int = 0,
    ctx: Context = None,
) -> str:
    """View compiler errors or warnings for a build, with source file and line info.

    Args:
        build_id: The CDash build ID.
        warnings: If True, show warnings instead of errors.
        limit: Maximum number of errors to return (default 30, max 200).
        offset: Number of errors to skip (default 0). Use for pagination.
    """
    client = _get_client(ctx)
    try:
        data = await client.get_build_errors(build_id, warnings=warnings)
    except CDashError as e:
        return f"Error: {e}"

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    label = "Warnings" if warnings else "Errors"
    lines: list[str] = []
    lines.append(f"# Build {label} (build_id={build_id})")
    lines.append("")

    errors = data.get("errors", [])
    if not errors:
        lines.append(f"No {label.lower()} found.")
        return "\n".join(lines)

    total = len(errors)
    page = errors[offset : offset + limit]

    if not page:
        lines.append(f"Found {total} {label.lower()} — no results in this range (offset={offset}).")
        return "\n".join(lines)

    lines.append(f"Found {total} {label.lower()} (showing {offset + 1}–{offset + len(page)}):")
    lines.append("")

    for err in page:
        source_file = err.get("sourcefile", "")
        source_line = err.get("sourceline", "")
        text = err.get("text", "").strip()
        precontext = err.get("precontext", "")
        postcontext = err.get("postcontext", "")

        if source_file:
            loc = f"{source_file}:{source_line}" if source_line else source_file
            lines.append(f"### {loc}")
        else:
            lines.append("### (no source location)")

        if precontext:
            lines.append(f"```\n{precontext}\n```")
        if text:
            # Truncate very long error messages
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(f"```\n{text}\n```")
        if postcontext:
            lines.append(f"```\n{postcontext}\n```")
        lines.append("")

    remaining = total - offset - len(page)
    if remaining > 0:
        lines.append(f"... {remaining} more (use offset={offset + limit} to see next page)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_build_tests
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_build_tests(
    build_id: int,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: Context = None,
) -> str:
    """List tests for a specific build, optionally filtered by status.

    Args:
        build_id: The CDash build ID.
        status_filter: Optional filter: "passed", "failed", or "notrun".
        limit: Maximum number of tests to return (default 50, max 200).
        offset: Number of tests to skip (default 0). Use for pagination.
    """
    client = _get_client(ctx)
    try:
        data = await client.get_build_tests(build_id, status_filter)
    except CDashError as e:
        return f"Error: {e}"

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    lines: list[str] = []
    filter_label = f" ({status_filter})" if status_filter else ""
    lines.append(f"# Tests for build {build_id}{filter_label}")
    lines.append("")

    tests = data.get("tests", [])
    if not tests:
        lines.append("No tests found.")
        return "\n".join(lines)

    total = len(tests)
    page = tests[offset : offset + limit]

    if not page:
        lines.append(f"Found {total} test(s) — no results in this range (offset={offset}).")
        return "\n".join(lines)

    lines.append(f"Found {total} test(s) (showing {offset + 1}–{offset + len(page)}):")
    lines.append("")

    for t in page:
        name = t.get("name", "?")
        status = t.get("status", "?")
        exec_time = t.get("execTime", "?")
        details = t.get("details", "")
        build_test_id = t.get("buildtestid", "")

        status_icon = {"Passed": "+", "Failed": "!", "Not Run": "-"}.get(
            status, "?"
        )
        line = f"- [{status_icon}] **{name}** ({status}, {exec_time}s)"
        if build_test_id:
            line += f" [buildtestid={build_test_id}]"
        if details:
            if len(details) > 150:
                details = details[:150] + "..."
            line += f" — {details}"
        lines.append(line)

    remaining = total - offset - len(page)
    if remaining > 0:
        lines.append(f"\n... {remaining} more (use offset={offset + limit} to see next page)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_configure_output
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_configure_output(
    build_id: int,
    ctx: Context = None,
) -> str:
    """View CMake configure command and output for a build.

    Args:
        build_id: The CDash build ID.
    """
    client = _get_client(ctx)
    try:
        data = await client.get_configure(build_id)
    except CDashError as e:
        return f"Error: {e}"

    lines: list[str] = []
    lines.append(f"# Configure Output (build_id={build_id})")
    lines.append("")

    configures = data.get("configures", [])
    if not configures:
        lines.append("No configure output found.")
        return "\n".join(lines)

    for conf in configures:
        command = conf.get("command", "")
        output = conf.get("output", "")
        status = conf.get("status", "?")

        status_label = "PASS" if str(status) == "0" else f"FAIL (status={status})"
        lines.append(f"**Status**: {status_label}")
        lines.append("")

        if command:
            lines.append("**Command**:")
            lines.append(f"```\n{command}\n```")
            lines.append("")

        if output:
            # Truncate very long output
            if len(output) > 5000:
                output = output[:5000] + "\n... (truncated, showing first 5000 chars)"
            lines.append("**Output**:")
            lines.append(f"```\n{output}\n```")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_test_details
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_test_details(
    build_test_id: int,
    ctx: Context = None,
) -> str:
    """Get detailed output/log for a single test run.

    Args:
        build_test_id: The CDash build-test ID (from get_build_tests results).
    """
    client = _get_client(ctx)
    try:
        data = await client.get_test_details(build_test_id)
    except CDashError as e:
        return f"Error: {e}"

    lines: list[str] = []

    test = data.get("test", {})
    test_name = test.get("test", test.get("name", "?"))
    status = test.get("status", "?")
    command = test.get("command", "")
    output = test.get("output", "")

    lines.append(f"# Test Details: {test_name}")
    lines.append(f"**Status**: {status}")
    lines.append(f"**Build-Test ID**: {build_test_id}")
    lines.append("")

    if command:
        lines.append("**Command**:")
        lines.append(f"```\n{command}\n```")
        lines.append("")

    # Measurements
    measurements = test.get("measurements", [])
    if measurements:
        lines.append("## Measurements")
        for m in measurements:
            name = m.get("name", "?")
            value = m.get("value", "?")
            lines.append(f"- **{name}**: {value}")
        lines.append("")

    if output:
        if len(output) > 8000:
            output = output[:8000] + "\n... (truncated, showing first 8000 chars)"
        lines.append("## Output")
        lines.append(f"```\n{output}\n```")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_test_summary
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_test_summary(
    project: str,
    test_name: str,
    date: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: Context = None,
) -> str:
    """Get summary of a test across builds — shows pass/fail history to detect flaky tests.

    Args:
        project: CDash project name (e.g. "PublicDashboard").
        test_name: Exact name of the test.
        date: Optional date (YYYY-MM-DD). Defaults to today.
        limit: Maximum number of builds to return (default 50, max 200).
        offset: Number of builds to skip (default 0). Use for pagination.
    """
    client = _get_client(ctx)
    try:
        data = await client.get_test_summary(project, test_name, date)
    except CDashError as e:
        return f"Error: {e}"

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    lines: list[str] = []
    lines.append(f"# Test Summary: {test_name}")
    lines.append("")

    num_failed = data.get("numfailed", 0)
    num_total = data.get("numtotal", 0)
    pct_passed = data.get("percentagepassed", 0)
    lines.append(
        f"**Results**: {num_total - num_failed}/{num_total} passed "
        f"({pct_passed:.1f}%)"
    )
    lines.append("")

    builds = data.get("builds", [])
    if not builds:
        lines.append("No build results found.")
        return "\n".join(lines)

    total = len(builds)
    page = builds[offset : offset + limit]

    if not page:
        lines.append(
            f"Results across {total} build(s)"
            f" — no results in this range (offset={offset})."
        )
        return "\n".join(lines)

    lines.append(f"## Results across {total} build(s) (showing {offset + 1}–{offset + len(page)}):")
    lines.append("")

    for b in page:
        site = b.get("site", "?")
        build_name = b.get("buildName", "?")
        status = b.get("status", "?")
        time_val = b.get("time", "?")
        build_id = b.get("buildid", "?")
        status_icon = {"Passed": "+", "Failed": "!", "Not Run": "-"}.get(
            status, "?"
        )

        line = (
            f"- [{status_icon}] **{status}** — {build_name} @ {site} "
            f"(build_id={build_id}, time={time_val}s)"
        )

        update = b.get("update", {})
        if update and update.get("revision"):
            line += f" rev={update['revision'][:12]}"
        lines.append(line)

    remaining = total - offset - len(page)
    if remaining > 0:
        lines.append(f"\n... {remaining} more (use offset={offset + limit} to see next page)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_build_update
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_build_update(
    build_id: int,
    ctx: Context = None,
) -> str:
    """View source code changes (VCS commits) associated with a build.

    Args:
        build_id: The CDash build ID.
    """
    client = _get_client(ctx)
    try:
        data = await client.get_build_update(build_id)
    except CDashError as e:
        return f"Error: {e}"

    lines: list[str] = []
    lines.append(f"# Source Updates (build_id={build_id})")
    lines.append("")

    update = data.get("update", {})
    if update:
        revision = update.get("revision", "")
        prior = update.get("priorrevision", "")
        if revision:
            lines.append(f"**Revision**: {revision}")
        if prior:
            lines.append(f"**Prior revision**: {prior}")
        diff_url = update.get("revisiondiff", "")
        if diff_url:
            lines.append(f"**Diff URL**: {diff_url}")
        lines.append("")

    update_groups = data.get("updategroups", [])
    if not update_groups:
        lines.append("No source changes found.")
        return "\n".join(lines)

    total_files = 0
    for group in update_groups:
        description = group.get("description", "Files")
        directories = group.get("directories", [])
        if not directories:
            continue

        lines.append(f"## {description}")
        lines.append("")

        for d in directories:
            dir_name = d.get("name", ".")
            files = d.get("files", [])
            for f in files:
                filename = f.get("filename", "?")
                author = f.get("author", "?")
                log = f.get("log", "").strip()
                revision = f.get("revision", "")

                path = f"{dir_name}/{filename}" if dir_name != "." else filename
                line = f"- `{path}` by **{author}**"
                if revision:
                    line += f" ({revision[:12]})"
                lines.append(line)
                if log:
                    if len(log) > 200:
                        log = log[:200] + "..."
                    lines.append(f"  {log}")
                total_files += 1

        lines.append("")

    if total_files == 0:
        lines.append("No source changes found.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_project_overview
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_project_overview(
    project: str,
    date: str | None = None,
    ctx: Context = None,
) -> str:
    """Get project overview with aggregate build/test/coverage statistics.

    Args:
        project: CDash project name (e.g. "PublicDashboard").
        date: Optional date (YYYY-MM-DD). Defaults to today.
    """
    client = _get_client(ctx)
    try:
        data = await client.get_project_overview(project, date)
    except CDashError as e:
        return f"Error: {e}"

    lines: list[str] = []
    title = data.get("title", f"{project} - Overview")
    lines.append(f"# {title}")
    lines.append("")

    has_sub = data.get("hasSubProjects", False)
    if has_sub:
        lines.append("*This project has subprojects.*")
        lines.append("")

    # Build groups
    groups = data.get("groups", [])
    if groups:
        group_names = [g.get("name", "?") for g in groups]
        lines.append(f"**Build groups**: {', '.join(group_names)}")
        lines.append("")

    # Coverage data
    coverages = data.get("coverages", [])
    if coverages:
        lines.append("## Coverage")
        for cov in coverages:
            name = cov.get("name", "?")
            lines.append(f"### {name}")
            current = cov.get("current", {})
            previous = cov.get("previous", {})
            if current:
                lines.append(f"  Current: {current}")
            if previous:
                lines.append(f"  Previous: {previous}")
        lines.append("")

    # Dynamic analysis
    dyn = data.get("dynamicanalyses", [])
    if dyn:
        lines.append("## Dynamic Analysis")
        for d in dyn:
            lines.append(f"- {d.get('name', '?')}")
        lines.append("")

    # Static analysis
    static = data.get("staticanalyses", [])
    if static:
        lines.append("## Static Analysis")
        for s in static:
            lines.append(f"- {s.get('name', '?')}")
        lines.append("")

    # Measurements
    measurements = data.get("measurements", [])
    if measurements:
        lines.append("## Measurements")
        for m in measurements:
            lines.append(f"- {m.get('name', '?')}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_coverage_comparison
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_coverage_comparison(
    project: str,
    date: str | None = None,
    build_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: Context = None,
) -> str:
    """Compare code coverage across builds for a project. Useful for detecting coverage regressions.

    Args:
        project: CDash project name (e.g. "PublicDashboard").
        date: Optional date (YYYY-MM-DD). Defaults to today.
        build_id: Optional build ID to get coverage for a specific build.
            Recommended: provide a build_id from the dashboard for reliable results.
            Without build_id, uses cross-build comparison (only works for Nightly builds).
        limit: Maximum number of files to return (default 50, max 200).
        offset: Number of files to skip (default 0). Use for pagination.
    """
    client = _get_client(ctx)
    try:
        data = await client.get_coverage_comparison(project, date, build_id)
    except CDashError as e:
        return f"Error: {e}"

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    lines: list[str] = []
    lines.append(f"# Coverage Comparison — {project}")
    lines.append("")

    total_records = data.get("iTotalRecords", 0)
    total_display = data.get("iTotalDisplayRecords", 0)

    lines.append(f"**Total files**: {total_records}")
    if total_display != total_records:
        lines.append(f"**Displayed**: {total_display}")
    lines.append("")

    rows = data.get("aaData", [])
    if not rows:
        lines.append("No coverage data found.")
        return "\n".join(lines)

    total = len(rows)
    page = rows[offset : offset + limit]

    if not page:
        lines.append(f"## Files ({total} total) — no results in this range (offset={offset}).")
        return "\n".join(lines)

    lines.append(f"## Files ({total} total, showing {offset + 1}–{offset + len(page)})")
    lines.append("")

    # CDash returns rows as arrays: [filename, status, percentage, untested, ...]
    for row in page:
        if len(row) >= 4:
            filename = row[0]
            # Strip HTML tags from filename
            filename_clean = re.sub(r"<[^>]+>", "", str(filename)).strip()
            status = re.sub(r"<[^>]+>", "", str(row[1])).strip()
            pct = re.sub(r"<[^>]+>", "", str(row[2])).strip()
            untested = re.sub(r"<[^>]+>", "", str(row[3])).strip()

            lines.append(f"- `{filename_clean}`: {status} ({pct}) — {untested}")
        else:
            lines.append(f"- {row}")

    remaining = total - offset - len(page)
    if remaining > 0:
        lines.append(f"\n... {remaining} more (use offset={offset + limit} to see next page)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_dynamic_analysis
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_dynamic_analysis(
    build_id: int,
    limit: int = 50,
    offset: int = 0,
    ctx: Context = None,
) -> str:
    """Get dynamic analysis results (e.g. Valgrind, sanitizers) for a build.

    Args:
        build_id: The CDash build ID.
        limit: Maximum number of defect entries to return (default 50, max 200).
        offset: Number of defect entries to skip (default 0). Use for pagination.
    """
    client = _get_client(ctx)
    try:
        data = await client.get_dynamic_analysis(build_id)
    except CDashError as e:
        return f"Error: {e}"

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    lines: list[str] = []
    title = data.get("title", f"Dynamic Analysis (build_id={build_id})")
    lines.append(f"# {title}")
    lines.append("")

    build = data.get("build", {})
    if build:
        lines.append(f"**Build**: {build.get('buildname', '?')}")
        lines.append(f"**Site**: {build.get('site', '?')}")
        lines.append(f"**Time**: {build.get('buildtime', '?')}")
        lines.append("")

    # Defect type legend
    defect_types = data.get("defecttypes", [])
    if defect_types:
        type_names = [d.get("type", "?") for d in defect_types]
        lines.append(f"**Defect types**: {', '.join(type_names)}")
        lines.append("")

    analyses = data.get("dynamicanalyses", [])
    if not analyses:
        lines.append("No dynamic analysis results found.")
        return "\n".join(lines)

    lines.append(f"## Results ({len(analyses)} tests)")
    lines.append("")

    # Show tests with defects first, then clean ones
    with_defects = []
    clean = 0
    for a in analyses:
        name = a.get("name", "?")
        status = a.get("status", "?")
        defects = a.get("defects", [])
        try:
            total_defects = sum(int(d) for d in defects)
        except (ValueError, TypeError):
            total_defects = 0

        if total_defects > 0:
            with_defects.append((name, status, defects, total_defects))
        else:
            clean += 1

    total = len(with_defects)
    page = with_defects[offset : offset + limit]

    if not page and total > 0:
        lines.append(f"{total} test(s) with defects — no results in this range (offset={offset}).")
    elif page:
        lines.append(f"Showing defects {offset + 1}–{offset + len(page)} of {total}:")
        lines.append("")
        for name, status, defects, total_defects in page:
            lines.append(f"- **{name}** [{status}] — {total_defects} defect(s)")

        remaining = total - offset - len(page)
        if remaining > 0:
            lines.append(
                f"\n... {remaining} more with defects"
                f" (use offset={offset + limit} to see next page)"
            )

    if clean:
        lines.append(f"\n{clean} test(s) with no defects (clean)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the CDash MCP server."""
    mcp.run()
