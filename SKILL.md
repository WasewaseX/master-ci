---
name: master-ci
description: Reusable CI template plus a zero-dependency MCP toolkit. Scan any repository for AI slop, preview and apply mechanical fixes, pre-check which CI stacks a change set fires, benchmark engine performance, diagnose the install, and check for updates. Use when the user asks to scan or fix a repo, asks what CI would run, mentions master-ci, MCP, AI slop, or performance, or wants fix-ready guidance.
license: MIT
---

# Master CI

Master CI is one engine with many surfaces: the same `ai_slop_detector.py`,
`fix_engine.py` and `path_filters.py` modules run in GitHub CI, in the
autofix/self-heal loops, and inside this MCP server. A scan you run here
reports exactly what CI would report, and a fix you apply here is exactly
what the automation would apply. There is no second implementation to
drift.

## Install (one command)

Clone the repository into your skills directory, pinned to the current
release tag (v2.11.1 - one clear stable baseline, kept in lockstep with
the MCP server version and the GitHub Release):

```bash
git clone --depth 1 --branch v2.11.1 https://github.com/WasewaseX/master-ci <skills-dir>/master-ci
```

MCP clients configure it the same way Claude Desktop does:

```json
{
  "mcpServers": {
    "master-ci": {
      "command": "python3",
      "args": ["<skills-dir>/master-ci/scripts/mcp_server.py"],
      "env": {"MASTER_CI_MCP_ROOT": "<repo the server may touch>"}
    }
  }
}
```

If your client has no MCP support, call the engines directly as CLI:
`python3 <skills-dir>/master-ci/scripts/ai_slop_detector.py --root <repo>`
for the scan and `python3 <skills-dir>/master-ci/scripts/fix_engine.py
--root <repo> --tier safe` for fixes. Pass `--enforce-bloat` to escalate
the structural-budget family (file size, parameter count, comment ratio,
banners, function length, nesting, chained dict-gets, cyclomatic and
cognitive complexity) from advisory to
blocking - the same flag the CI template's `strict` profile passes, and
the same standards the template's own codebase passes under.

## Tools

| Tool | Mutates? | Purpose |
|---|---|---|
| `scan_slop` | no | Score a path (0-100, grade, verdict), list findings with remedies, show whether CI would gate. Since v1.18 findings are bucketed into seven quality dimensions (architecture, code health, maintainability, security, test assurance, dependency health, AI integrity) and the slop-score ceiling gates the AI-integrity dimension only. |
| `scan_diff` | no | Score only the changed files of a git repo; report which CI stacks fire. |
| `plan_fixes` | no | Dry-run the fix policy: planned tools, tiers, files the normalizer would touch. |
| `apply_fixes` | **yes** | Run the fix policy in place (safe tier by default, lint tier opt-in). Requires `MASTER_CI_MCP_ROOT`; refuses to run without it. |
| `list_checks` | no | The check catalog: rules, phrase database, remedies, scoring, fix policy. |
| `filter_paths` | no | Which CI stacks a change set would activate, per file. |
| `diagnose` | no | Install report: versions, engines, missing formatters, self-test command. |
| `bench` | no | Performance numbers of the engines on a deterministic corpus; baseline/check guidance. |
| `check_updates` | network | Compare installed version with the newest GitHub tag; return the update command. |

Every tool result ends with a `prompt` field: the fix-ready next step.
Follow it instead of guessing what to do with the data.

Prompts (via `prompts/get`): `fix-my-repo` (full loop), `explain-findings`
(reviewer-style triage), `pre-flight` (before pushing).

## The fix loop

1. `scan_slop` on the target path; read score, `blocking_gate`, findings.
2. Plan with `plan_fixes`; never mutate on a dry-run's word alone.
3. `apply_fixes` with `tiers: ["safe"]` (add `"lint"` only when the user
   agreed to tool-verified transforms).
4. Re-run `scan_slop` and `plan_fixes`: blocking score down, plan reports
   zero files changed.
5. Show `git diff --stat` and summarize what changed and why.

Never auto-fix: secrets (rotate them), dependency vulnerabilities (upgrade
is a decision), prose findings (meaning-level rewrites are deliberate
edits, not mechanical ones).

## Updates

Nothing updates automatically, ever. Run `check_updates` (or
`python3 scripts/mcp_ping.py --online`) to compare against the newest tag,
then update explicitly: `git -C <skills-dir>/master-ci pull --ff-only`.

## Trust it

`python3 scripts/mcp_ping.py` spawns the server and drives a real MCP
session (initialize, tools/list, diagnose, prompts, `server/discover`
with the 2026-07-28 per-request `_meta` protocol, and the apply_fixes
write-lock refusal); exit 0 means the install works. The same wire
protocol is covered by the repo's pytest suite under
tests/test_mcp_server.py.

Verification does not stop at this repo's own tests: the
[ci-canary](https://github.com/WasewaseX/ci-canary) repository is an
independent consumer that calls the reusable workflow from a foreign
project on a schedule, and master-ci's watchdog workflow asserts that
canary stays green daily.

## Safety

`apply_fixes` is the only mutating tool and only touches the working tree
you point it at. `MASTER_CI_MCP_ROOT` is MANDATORY for it: the tool
refuses to mutate anything until the server is pinned to one repository,
and with the lock set every path must resolve inside it. The safe tier
can only reformat; the lint tier only applies tool-verified transforms;
the server has no secrets and never pushes.
