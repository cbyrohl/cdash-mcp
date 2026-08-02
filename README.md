# cdash-mcp

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

An [MCP](https://modelcontextprotocol.io/) server for [Kitware CDash](https://www.cdash.org/) — the CI/CD dashboard for projects built with CMake/CTest. Browse dashboards, find failing tests, inspect build errors, check coverage, and triage CI failures, all through natural language. Works with OpenAI Codex, Claude Desktop/Code, Cursor, and any MCP-compatible client.

Provides 11 tools for navigating CDash builds, tests, coverage, and dynamic analysis.

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

## Tools (11)

### Dashboard & Overview

| Tool | Description |
|------|-------------|
| `get_dashboard` | Dashboard overview: build groups, pass/fail counts, build IDs |
| `get_project_overview` | Aggregate build/test/coverage statistics for a project |

### Test Triage

| Tool | Description |
|------|-------------|
| `get_failing_tests` | Find non-passing tests across all builds (CI triage entry point) |
| `get_build_tests` | List tests for a specific build, filter by passed/failed/notrun |
| `get_test_summary` | Test pass/fail history across builds — detect flaky tests |

### Build Inspection

| Tool | Description |
|------|-------------|
| `get_build_details` | Drill into a build: configure/compile/test summary |
| `get_build_errors` | Compiler errors or warnings with source file and line info |
| `get_configure_output` | CMake configure command and output |
| `get_build_update` | Source code changes (VCS commits) associated with a build |

### Coverage & Analysis

| Tool | Description |
|------|-------------|
| `get_coverage_comparison` | Compare code coverage across builds, detect regressions |
| `get_dynamic_analysis` | Dynamic analysis results (Valgrind, sanitizers) |

## Troubleshooting

**401 Authentication errors:**
- Verify your token is valid in CDash under My Profile > Authentication Token.
- For Codex, check `~/.codex/config.toml` (or `.codex/config.toml` for a trusted project), then start a new session and inspect `/mcp`.
- Make sure the `env` block is in the right config file. For Claude Code, MCP servers must be defined in `~/.claude.json` — putting them in `~/.claude/settings.json` will silently ignore the env vars.
- After changing config, restart the MCP server (`/mcp` in Codex or Claude Code, or restart the application).

**Project not found / empty dashboard:**
- CDash project names are case-sensitive. Check the exact name in your CDash instance.

## Development

```bash
# Install dev dependencies
uv sync

# Run tests (some tests hit a live CDash instance)
uv run pytest tests/ -v

# Lint
uv run ruff check src/ tests/

# Run the server locally
uv run cdash-mcp
```

## License

MIT
