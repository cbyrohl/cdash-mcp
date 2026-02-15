# cdash-mcp

MCP server for querying [Kitware CDash](https://www.cdash.org/) CI/CD build and test results.

Lets AI assistants (Claude Code, Claude Desktop) browse dashboards, find failing tests, and inspect build errors from any CDash instance.

## Install

```bash
git clone https://github.com/cbyrohl/cdash-mcp.git && cd cdash-mcp
uv sync
```

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `CDASH_URL` | `https://open.cdash.org` | CDash instance URL |
| `CDASH_TOKEN` | _(none)_ | Bearer token for authentication (required for private instances) |

> **Note:** Project names in CDash are case-sensitive (e.g. `"thor"` and `"THOR"` are different projects).

## Usage

### Claude Code

The easiest way to add the server:

```bash
claude mcp add cdash \
  -e CDASH_URL=https://open.cdash.org \
  -e CDASH_TOKEN=your-token-here \
  -- uv --directory /path/to/cdash-mcp run cdash-mcp
```

This writes the config to `~/.claude.json`, which is where Claude Code reads MCP server definitions from.

Alternatively, add the JSON manually to `~/.claude.json` (**not** `~/.claude/settings.json` — env vars will be silently ignored there):

```json
{
  "mcpServers": {
    "cdash": {
      "command": "uv",
      "args": ["--directory", "/path/to/cdash-mcp", "run", "cdash-mcp"],
      "env": {
        "CDASH_URL": "https://open.cdash.org",
        "CDASH_TOKEN": "your-token-here"
      }
    }
  }
}
```

You can also use a project-scoped `.mcp.json` in your repo root, which supports environment variable expansion:

```json
{
  "mcpServers": {
    "cdash": {
      "command": "uv",
      "args": ["--directory", "/path/to/cdash-mcp", "run", "cdash-mcp"],
      "env": {
        "CDASH_URL": "${CDASH_URL:-https://open.cdash.org}",
        "CDASH_TOKEN": "${CDASH_TOKEN}"
      }
    }
  }
}
```

### Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "cdash": {
      "command": "uv",
      "args": ["--directory", "/path/to/cdash-mcp", "run", "cdash-mcp"],
      "env": {
        "CDASH_URL": "https://open.cdash.org",
        "CDASH_TOKEN": "your-token-here"
      }
    }
  }
}
```

### MCP Inspector (manual testing)

```bash
CDASH_URL=https://open.cdash.org CDASH_TOKEN=your-token-here \
  npx @modelcontextprotocol/inspector uv run cdash-mcp
```

## Tools

| Tool | Description |
|---|---|
| `get_dashboard` | Dashboard overview: build groups, pass/fail counts, build IDs |
| `get_failing_tests` | Non-passing tests across all builds (CI triage) |
| `get_build_details` | Drill into a build: configure/compile/test summary |
| `get_build_errors` | Compiler errors/warnings with source file:line |
| `get_build_tests` | List tests for a build, filter by passed/failed/notrun |
| `get_configure_output` | CMake configure command and output |

## Troubleshooting

**401 Authentication errors:**
- Verify your token is valid in CDash under My Profile > Authentication Token.
- Make sure the `env` block is in the right config file. For Claude Code, MCP servers must be defined in `~/.claude.json` — putting them in `~/.claude/settings.json` will silently ignore the env vars.
- After changing config, restart the MCP server (`/mcp` in Claude Code, or restart the application).

**Project not found / empty dashboard:**
- CDash project names are case-sensitive. Check the exact name in your CDash instance.

## Development

```bash
# Run tests (hits open.cdash.org live)
uv run pytest tests/ -v

# Start server locally
uv run cdash-mcp
```
