# Master CI MCP server

`scripts/mcp_server.py` exposes the Master CI engines over the Model
Context Protocol so any MCP-capable AI assistant can use them: Claude
Desktop, Cursor, Cline, Windsurf, z.ai, or any client that speaks MCP.
The server is stdlib-only (no SDK, no pip install, no required network)
and speaks MCP over stdio with newline-delimited JSON-RPC 2.0. The
repository root carries a `SKILL.md`, so the same install doubles as a
skill that non-MCP assistants can load and follow.

The guiding rule is the same one vibecheck applies: the MCP server wraps
the exact engines CI runs - `ai_slop_detector.py`, `fix_engine.py`,
`path_filters.py` - so a scan over MCP reports exactly what CI reports,
and a fix over MCP applies exactly what the autofix and self-heal loops
apply. There is no second implementation to drift. Every tool result also
embeds a fix-ready `prompt` field: the next instruction the assistant
should execute, so data never arrives without a plan.

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
| `MASTER_CI_MCP_ROOT` | Lock every path argument to resolve inside this directory. REQUIRED for `apply_fixes`: the mutating tool refuses to run until this is set, and with it set every path must stay inside. Read-only tools work without it. |

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

### `diagnose` (read-only)

The install report: server and protocol versions, Python version, the
loaded engines, which external formatters are on PATH and which are
missing, the active `MASTER_CI_MCP_ROOT` lock, and the one-command self
test. Missing formatters only narrow `apply_fixes`; the scan tools are
stdlib and always work. This is the first tool to call when something
behaves oddly.

### `bench` (read-only)

Runs the performance benchmark (`scripts/benchmark.py`) of the loaded
engines against a deterministic synthetic corpus (fixed seed, identical
on every machine): detector throughput, fix-engine plan and generic scan
times, path-filter throughput. Returns the metrics with units plus a
`prompt` that explains how to save a baseline and gate a release with
`--check`. MCP roundtrips are excluded inside the tool; run the script
directly for the full suite including MCP startup and stdio timings.

### `check_updates` (network)

Compares the installed `SERVER_VERSION` against the newest semver tag on
GitHub and returns the exact update command for the install type. Offline
or rate-limited is reported gracefully as an `error` field, never a
crash. The server never updates itself and never pushes anything; the
only network call in the entire codebase lives behind this tool.

## Prompts

Three ready-made walkthroughs ship through the MCP prompts primitive
(`prompts/list`, `prompts/get`), next to the per-result `prompt` fields:

- `fix-my-repo`: the full loop as one paste-ready instruction (scan,
  deliberate prose edits, plan, apply, verify, report).
- `explain-findings`: reviewer-style triage of a scan - what is
  mechanical, what needs real edits, what CI would do.
- `pre-flight`: before pushing, which CI stacks fire and how the changed
  files score.

## Updates

Nothing on a user's device updates automatically, ever. That is
deliberate: silent auto-update would be a supply chain risk and would
contradict the zero-surprise model of the rest of the template. The
installed copy tells you when it is stale instead. Call `check_updates`
over MCP, run `python3 scripts/mcp_ping.py --online`, or compare against
<https://github.com/WasewaseX/master-ci/releases> manually. To update:

```bash
git -C <install-dir> pull --ff-only
git -C <install-dir> fetch --tags --force
```

## Trust

The wire protocol is what you should trust, and it is tested as such.
`tests/test_mcp_server.py` spawns the server as a real subprocess and
drives a full session over real stdio: initialize negotiation, the
initialized notification, ping, tools/list schema checks, a mutating
tools/call over a fixture repository, the path lock, parse errors and
method-not-found codes. The suite runs in CI on every push. On any
device, after any install, run:

```bash
python3 scripts/mcp_ping.py            # full wire-level self test
python3 scripts/mcp_ping.py --online   # plus the update check
```

Exit code 0 means the install speaks MCP correctly; the same traffic is
what Claude Desktop, Cursor or z.ai produce.

## Protocol notes

The server speaks the full MCP timeline:

- **Legacy handshake era** (2024-11-05, 2025-03-26, 2025-06-18 and
  2025-11-25): clients call `initialize` first; the server echoes the
  requested version if supported and falls back to 2024-11-05 otherwise.
- **Modern stateless era** (2026-07-28): no handshake. Every request
  carries its protocol version in `params._meta["io.modelcontextprotocol/
  protocolVersion"]`, and `server/discover` replaces `initialize` as the
  capability query - it returns `resultType: complete`, the full
  `supportedVersions` list, the capabilities object and the serverInfo
  meta block. A request with an unknown per-request version is rejected
  with the standard `-32022 UnsupportedProtocolVersionError` whose `data`
  lists the versions this server supports.

Both eras can be mixed freely on one connection. Also supported: `ping`,
`tools/list`, `tools/call`, `prompts/list`, `prompts/get`, and
`resources/list` (empty). Notifications are never answered. Malformed
lines get a `-32700` error, unknown methods `-32601`, unknown tools or
prompts `-32602`, and tool execution failures come back as
`isError: true` results - the server itself never crashes on a bad
message.

## Security model

The server has no secrets, never pushes anything, and its only network
access is the `check_updates` tool. `apply_fixes` is the only mutating
tool and it is double-gated: it refuses to run at all unless
`MASTER_CI_MCP_ROOT` is set (enforced, not recommended), and it rejects
any path outside that root. Reads stay flexible; writes cannot happen
without an explicitly declared repository. The lint tier runs
`semgrep --autofix`, which only applies fixes that ship with registry
rules you explicitly pass in `semgrep_config`.

## Supply chain

One file, zero dependencies, nothing to install: the server imports the
stdlib plus its sibling engine scripts from the same checkout, so there
is no package manager, no postinstall hook, no transitive dependency and
no lockfile to audit. Upgrades are git pulls of a tagged release, and the
SHA-pinned GitHub Actions in the workflows follow the same discipline.
The engines themselves are the exact files CI runs, verified by the
parity tests in `tests/test_workflow_contracts.py`.
