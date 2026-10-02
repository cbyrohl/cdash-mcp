# cdash-mcp

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

An [MCP](https://modelcontextprotocol.io/) server for [Kitware CDash](https://www.cdash.org/) — the CI/CD dashboard for projects built with CMake/CTest. Browse dashboards, find failing tests, inspect build errors, check coverage, and triage CI failures, all through natural language. Works with OpenAI Codex, Claude Desktop/Code, Cursor, and any MCP-compatible client.

Provides 26 read-only tools for navigating CDash builds, tests, coverage, and dynamic analysis.

## Quick Start

### Prerequisites

- Python 3.12+
- A CDash instance (defaults to [my.cdash.org](https://my.cdash.org))

### Installation

```bash
# Install from GitHub with uv (recommended)
uv tool install git+https://github.com/cbyrohl/cdash-mcp

# Or with pip
pip install git+https://github.com/cbyrohl/cdash-mcp
```

### OpenAI Codex

Add the server to Codex with the CLI:

```bash
codex mcp add cdash \
  --env CDASH_URL=https://my.cdash.org \
  --env CDASH_TOKEN=your-token-here \
  -- uvx --from git+https://github.com/cbyrohl/cdash-mcp cdash-mcp

# Confirm that Codex stored the configuration
codex mcp list
```

The Codex CLI, IDE extension, and ChatGPT desktop app share this MCP configuration. Omit the `CDASH_TOKEN` option for public instances. Start a new Codex session after adding the server, then use `/mcp` to inspect its tools.

For project-scoped configuration, export your credentials and add this to `.codex/config.toml` in a trusted repository:

```bash
export CDASH_URL=https://my.cdash.org
export CDASH_TOKEN=your-token-here
```

```toml
[mcp_servers.cdash]
command = "uvx"
args = ["--from", "git+https://github.com/cbyrohl/cdash-mcp", "cdash-mcp"]
env_vars = ["CDASH_URL", "CDASH_TOKEN"]
```

See the [Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp) for additional configuration and tool-policy options.

### Claude Code

```bash
claude mcp add cdash \
  -e CDASH_URL=https://my.cdash.org \
  -e CDASH_TOKEN=your-token-here \
  -- uvx --from git+https://github.com/cbyrohl/cdash-mcp cdash-mcp
```

Use `--scope user` for global access, `--scope project` to share via `.mcp.json` in your repo, or omit `--scope` for local (current project only).

### Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "cdash": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/cbyrohl/cdash-mcp", "cdash-mcp"],
      "env": {
        "CDASH_URL": "https://my.cdash.org",
        "CDASH_TOKEN": "your-token-here"
      }
    }
  }
}
```

### Running from Source

```bash
git clone https://github.com/cbyrohl/cdash-mcp.git
cd cdash-mcp
uv sync

# Run the server
uv run cdash-mcp
```

## Configuration

| Environment Variable | Required | Default | Description |
|---------------------|----------|---------|-------------|
| `CDASH_URL` | No | `https://my.cdash.org` | CDash instance URL |
| `CDASH_TOKEN` | No | — | Bearer token for authentication (required for private instances) |

> **Note:** Project names in CDash are case-sensitive (e.g. `"thor"` and `"THOR"` are different projects).

## API compatibility and authentication

The modern client uses GraphQL for build inspection and supported REST endpoints
for dashboards and project-wide test queries. Tested against `my.cdash.org`
CDash 5.4. The legacy build summary, compiler error, configure, test history,
source update, and coverage URL paths are no longer called. Older CDash versions
are not automatically supported: use `check_connection` to inspect available
fields, and report unsupported fields rather than treating them as empty results.

Private projects require a valid, unexpired **full-access** token belonging to a
user with project access. Submission-only tokens cannot authenticate this client.
The client first calls a REST endpoint to establish a session cookie, then uses
that cookie with GraphQL. Session authentication is retried once when access is
denied; persistent permission failures remain errors. No token is included in
client representations or tool results.

All tools are read-only. API failures set the MCP error flag. Results are JSON
objects rather than the previous Markdown summaries, and use current CDash
field names. IDs from GraphQL are strings. Test-query REST results additionally
include numeric `build_id` and `test_id` fields and navigable URLs.

## Tools (26)

| Tool | Purpose |
|------|---------|
| `check_connection` | Authentication, project access, server version and available build fields |
| `get_dashboard` | Dashboard build groups and configure/compile/test counts |
| `get_project_overview` | Project coverage, analysis and build-group overview |
| `search_builds` | Build history by name, UTC date range, type or exact revision |
| `get_build_details` | Build counts, durations, site, compiler and revision |
| `get_build_triage` | Summary, configure log, compiler errors and failed tests in one call |
| `get_build_errors` | Errors or warnings, source locations, commands and retrievable context |
| `get_configure_output` | Configure command, return value and log |
| `get_build_update` | Revision, prior revision and update status |
| `get_update_files` | Changed files, authors and commit messages |
| `get_failing_tests` | Non-passing test results on a CDash date, with build/test IDs |
| `get_build_tests` | Per-build tests, status filters and timing statistics |
| `get_test_details` | Restored test command, measurements, status and output |
| `get_test_images` | Submitted regression images and their URLs |
| `get_test_summary` | Exact-name results across builds on one CDash date |
| `get_test_history` | Exact-name history across an optional UTC build-date range |
| `compare_builds` | New/fixed test failures, statuses, runtimes and build counts |
| `get_build_coverage` | File line, branch and function coverage |
| `get_coverage_file` | Exact file's source (when permitted) and line hit counts |
| `compare_build_coverage` | Explicit build pair, changed/added/removed files and weighted line totals |
| `get_coverage_comparison` | Compatibility alias requiring an explicit `build_id` |
| `get_dynamic_analysis` | Checker, command and per-type defect counts |
| `get_dynamic_analysis_details` | Individual analysis log and defects |
| `get_build_notes` | Submitted notes with retrievable text |
| `get_build_artifacts` | Uploaded files/download URLs or submitted links |
| `get_build_commands` | Submitted CMake command timings and resource measurements |

### Pagination and output

Build-scoped test, compiler-diagnostic, coverage, instrumentation and dynamic-analysis
lists merge the selected build's records with all immediate child builds. Each record
includes `build_id` and `subproject` (ID/name or null). Parent results come first,
then children in ID order, with each relation preserving its own pagination cursor.
The child-build catalog is also paginated. Continuation cursors are opaque and bound
to the selected build, relation, filter and child catalog; changing those invalidates
the cursor. A final continuation page can be empty when remaining children have no
matching records.

GraphQL lists return `items` and `page_info`. Pass `page_info.endCursor` as
`after` when `hasNextPage` is true. Page sizes are 1–200. Existing per-build
`offset` arguments remain supported by traversing cursors; do not combine
`offset` and `after`. REST test queries retain local `limit`/`offset` slicing and
identify that explicitly in their results.

Logs use `output_offset` and `output_limit` (default 34,816 characters). A sliced
text object includes `text`, `total_characters`, `offset` and `next_offset`.
Use `next_offset` to continue, or `output_limit=0` for the complete text.

Build searches and multi-date history use inclusive UTC calendar dates; dashboard
and test-summary dates follow CDash's configured dashboard day. These can differ
when the nightly rollover is not midnight.

Comparisons fetch all relevant records, up to 10,000 per build, before computing
changes. Larger results fail explicitly. Duplicate test names within the same subproject also fail rather
than guessing which results correspond. Comparisons match tests and files by
subproject ID plus name/path, so identically named records in different subprojects
remain separate. Coverage totals count each submitted subproject record. A fixed failure means `FAILED` became
`PASSED`; removed or skipped tests are not counted as fixes. Coverage percentages
are weighted by executable line counts, and file comparisons do not compute
patch coverage.

CMake instrumentation and artifacts are available only when submitted by CI.
Nested command measurements expose their own pagination information; this tool
currently retrieves the first 20 measurements per command.

## Troubleshooting

**Authentication or authorization errors:**
- Verify your token is valid, unexpired and full-access in CDash under My Profile > Authentication Token.
- Run `check_connection` with your project name to verify authentication and membership.
- For Codex, check `~/.codex/config.toml` (or `.codex/config.toml` for a trusted project), then start a new session and inspect `/mcp`.
- Make sure the `env` block is in the right config file. For Claude Code, MCP servers must be defined in `~/.claude.json` — putting them in `~/.claude/settings.json` will silently ignore the env vars.
- After changing config, restart the MCP server (`/mcp` in Codex or Claude Code, or restart the application).

**Project not found / empty dashboard:**
- CDash project names are case-sensitive. Check the exact name in your CDash instance.

## Development

```bash
# Install dev dependencies
uv sync

# Run deterministic tests, including MCP and STDIO transport (no network)
uv run pytest -v

# Opt in to live smoke tests; export CDASH_TOKEN securely if needed
CDASH_URL=https://open.cdash.org CDASH_LIVE_PROJECT=PublicDashboard \
  uv run pytest -m integration -v

# Lint
uv run ruff check src/ tests/

# Run the server locally
uv run cdash-mcp
```

## License

MIT
