"""FastMCP server exposing CDash CI/CD data as tools. [AI-Claude]"""

import logging
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
    ctx: Context = None,
) -> str:
    """Find non-passing tests across all builds for a project. Most useful for CI triage.

    Args:
        project: CDash project name (e.g. "PublicDashboard").
        date: Optional date (YYYY-MM-DD). Defaults to today.
        test_name: Optional filter to match test names containing this string.
    """
    client = _get_client(ctx)
    try:
        data = await client.query_tests(project, date, test_name)
    except CDashError as e:
        return f"Error: {e}"

    lines: list[str] = []
    lines.append(f"# Failing Tests for {project}")
    lines.append("")

    tests = data.get("builds", [])
    if not tests:
        lines.append("No failing tests found.")
        return "\n".join(lines)

    lines.append(f"Found {len(tests)} non-passing test result(s):")
    lines.append("")

    # Cap at 50 results
    for t in tests[:50]:
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

    if len(tests) > 50:
        lines.append(f"... and {len(tests) - 50} more results (showing first 50)")

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
    ctx: Context = None,
) -> str:
    """View compiler errors or warnings for a build, with source file and line info.

    Args:
        build_id: The CDash build ID.
        warnings: If True, show warnings instead of errors.
    """
    client = _get_client(ctx)
    try:
        data = await client.get_build_errors(build_id, warnings=warnings)
    except CDashError as e:
        return f"Error: {e}"

    label = "Warnings" if warnings else "Errors"
    lines: list[str] = []
    lines.append(f"# Build {label} (build_id={build_id})")
    lines.append("")

    errors = data.get("errors", [])
    if not errors:
        lines.append(f"No {label.lower()} found.")
        return "\n".join(lines)

    lines.append(f"Found {len(errors)} {label.lower()}:")
    lines.append("")

    # Cap at 30 errors
    for err in errors[:30]:
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

    if len(errors) > 30:
        lines.append(f"... and {len(errors) - 30} more (showing first 30)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_build_tests
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_build_tests(
    build_id: int,
    status_filter: str | None = None,
    ctx: Context = None,
) -> str:
    """List tests for a specific build, optionally filtered by status.

    Args:
        build_id: The CDash build ID.
        status_filter: Optional filter: "passed", "failed", or "notrun".
    """
    client = _get_client(ctx)
    try:
        data = await client.get_build_tests(build_id, status_filter)
    except CDashError as e:
        return f"Error: {e}"

    lines: list[str] = []
    filter_label = f" ({status_filter})" if status_filter else ""
    lines.append(f"# Tests for build {build_id}{filter_label}")
    lines.append("")

    tests = data.get("tests", [])
    if not tests:
        lines.append("No tests found.")
        return "\n".join(lines)

    lines.append(f"Found {len(tests)} test(s):")
    lines.append("")

    # Cap at 50
    for t in tests[:50]:
        name = t.get("name", "?")
        status = t.get("status", "?")
        exec_time = t.get("execTime", "?")
        details = t.get("details", "")

        status_icon = {"Passed": "+", "Failed": "!", "Not Run": "-"}.get(
            status, "?"
        )
        line = f"- [{status_icon}] **{name}** ({status}, {exec_time}s)"
        if details:
            if len(details) > 150:
                details = details[:150] + "..."
            line += f" — {details}"
        lines.append(line)

    if len(tests) > 50:
        lines.append(f"\n... and {len(tests) - 50} more (showing first 50)")

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
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Run the CDash MCP server."""
    mcp.run()
