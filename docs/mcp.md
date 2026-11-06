# Master CI MCP server

`scripts/mcp_server.py` exposes the Master CI engines over the Model
Context Protocol so any MCP-capable AI assistant can use them: Claude
Desktop, Cursor, Cline, Windsurf, z.ai, or any client that speaks MCP.
The server is stdlib-only (no SDK, no pip install, no network) and speaks
MCP over stdio with newline-delimited JSON-RPC 2.0.

The guiding rule is the same one vibecheck applies: the MCP server wraps
the exact engines CI runs - `ai_slop_detector.py`, `fix_engine.py`,
`path_filters.py` - so a scan over MCP reports exactly what CI reports,
and a fix over MCP applies exactly what the autofix and self-heal loops
apply. There is no second implementation to drift.

## Configuration

Claude Desktop (`claude_desktop_config.json`), Cursor (`.cursor/mcp.json`),
Cline, Windsurf and z.ai-compatible clients all use the same shape:

```json
{
  "mcpServers": {
    "master-ci": {
      "command": "python3",
      "args": ["/path/to/master-ci/scripts/mcp_server.py"]
    }
  }
}
```

Optional environment variables:

| Variable | Effect |
|---|---|
| `MASTER_CI_MCP_ROOT` | Lock every path argument to resolve inside this directory. Recommended when the server should only ever touch one repository. |

To serve a different repository, point the client's `cwd` at it or pass
`path` arguments per call. `filter_paths` reads the live filter table from
`<repo>/.github/workflows/master-ci.yml` when it exists and falls back to
the committed snapshot otherwise.

## Tools

### `scan_slop` (read-only)

Runs the AI-integrity engine on a file or directory. Returns the project
score (0-100), letter grade, verdict label, the blocking score CI gates
on (`would_fail_ci` against the `max_score` you pass), the advisory
score, and every finding with path, line, check id, message, weight and
the machine-applicable remedy from the detector's remedy table.

```json
{"name": "scan_slop", "arguments": {"path": "/repo", "max_score": 8}}
```

### `scan_diff` (read-only)

Scans only the changed files of a git repository. Default: uncommitted
changes vs HEAD. With `base: "main"` it diffs against a ref. The response
also includes `stacks_fired`: which CI stacks the change set activates
according to the live path-filter table.

### `plan_fixes` (read-only)

Dry-run of the fix policy: the planned tools with their tier and the
count of files the generic whitespace normalizer would touch. Never
mutates anything.

### `apply_fixes` (mutates)

Executes the fix policy in place. Default tier `safe` (formatters and the
whitespace normalizer - cannot change program behavior); pass
`"tiers": ["safe", "lint"]` to also apply tool-verified transforms
(`ruff check --fix`, `go mod tidy`, `sqlfluff fix`, `semgrep --autofix`
with the packs you pass in `semgrep_config`). The response carries the
per-tool manifest and the same summary table the autofix PRs embed.
Review with `git diff` afterwards; closing nothing is needed - the AI
changed your working tree, you commit what you want.

### `list_checks` (read-only)

The check catalog as structured data: blocking vs advisory detector rules,
the full phrase database, the per-check remedy map, the scoring formula,
and the fix policy tiers with the never-auto-fixed list.

### `filter_paths` (read-only)

"Which checks would run for these files?" - pass repo-relative paths, get
the fired and idle filters plus a per-file breakdown. Uses the same
picomatch-subset translator as the path-filter regression suite (one
implementation, guaranteed by tests), so the answer matches what
dorny/paths-filter will decide in CI.

## Protocol notes

The server supports `initialize` (protocol versions 2024-11-05, 2025-03-26
and 2025-06-18; unknown versions fall back to 2024-11-05), `ping`,
`tools/list`, `tools/call`, and answers `prompts/list` and
`resources/list` with empty lists. Notifications are never answered.
Malformed lines get a `-32700` error, unknown methods `-32601`, and tool
failures come back as `isError: true` results - the server itself never
crashes on a bad message.

## Security model

The server has no secrets, makes no network calls, and never pushes
anything. `apply_fixes` is the only mutating tool and it mutates only the
working tree you point it at - set `MASTER_CI_MCP_ROOT` to pin it. The
lint tier runs `semgrep --autofix`, which only applies fixes that ship
with registry rules you explicitly pass in `semgrep_config`.
