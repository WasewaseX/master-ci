#!/usr/bin/env python3
"""mcp_server.py - Model Context Protocol server for Master CI.

Any MCP-capable AI assistant (Claude Desktop, Cursor, Cline, Windsurf,
z.ai and friends) can now drive the Master CI engines directly: scan for
AI slop, ask which checks a change set would trigger, dry-run the fix
policy, apply it, and read the check catalog. One engine, many surfaces -
the same philosophy vibecheck uses, applied to the whole template. Every
tool result embeds a fix-ready `prompt` field: the next instruction the
assistant should execute, so data never arrives without a plan. Ready
made walkthroughs are also served through the MCP prompts primitive.

Two meta tools keep an install trustworthy. `diagnose` reports the
server version, the loaded engines, and which external formatters are on
PATH. `check_updates` compares the installed version against the newest
tag on GitHub and returns the exact command to update - the server never
updates anything by itself, and it never touches the network except when
this tool is called.

Transport is stdio with newline-delimited JSON-RPC 2.0, exactly as the
MCP spec defines it. The server is stdlib-only: no SDK, no pip install,
no required network. Launch it with `python3 scripts/mcp_server.py` and
point any client's mcpServers config at it; `scripts/mcp_ping.py` is a
standalone client that proves an install works end to end.

Tools are thin wrappers over the real engines (ai_slop_detector.py,
fix_engine.py, path_filters.py), so an MCP scan reports exactly what CI
would report and an MCP fix applies exactly what the autofix loop would
apply. `apply_fixes` is the only mutating tool; everything else is
read-only. If the MASTER_CI_MCP_ROOT environment variable is set, every
path argument must resolve inside that directory - a simple lock for
users who want the server pinned to one repository.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
SERVER_VERSION = "1.5.0"
DEFAULT_REPO_SLUG = "WasewaseX/master-ci"
DEFAULT_GH_API = "https://api.github.com"
UPDATE_TIMEOUT_SECONDS = 10.0
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
MAX_FINDINGS_IN_RESPONSE = 200
SNAPSHOT_MAX_CHANGED = 50
SNAPSHOT_SKIP_DIRS = frozenset(
    {".git", "node_modules", "vendor", "dist", "target", "__pycache__", ".venv", "venv"}
)
EXTERNAL_TOOLS = (
    "ruff",
    "rustfmt",
    "gofmt",
    "biome",
    "shfmt",
    "markdownlint",
    "sqlfluff",
    "semgrep",
    "pytest",
    "git",
)


def _load(name: str):
    """Import a sibling engine module by path (works from any cwd)."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


detector = _load("ai_slop_detector")
fix_engine = _load("fix_engine")
path_filters = _load("path_filters")


# ---------------------------------------------------------------------------
# path safety
# ---------------------------------------------------------------------------


def _resolve(path: str) -> Path:
    """Resolve a tool path argument and enforce the optional root lock."""
    root_lock = os.environ.get("MASTER_CI_MCP_ROOT")
    resolved = Path(path).expanduser().resolve()
    if root_lock:
        locked = Path(root_lock).expanduser().resolve()
        if resolved != locked and locked not in resolved.parents:
            raise ValueError(f"path {resolved} is outside MASTER_CI_MCP_ROOT ({locked})")
    if not resolved.exists():
        raise ValueError(f"path does not exist: {resolved}")
    return resolved


# ---------------------------------------------------------------------------
# tool implementations - each returns a JSON-serializable payload
# ---------------------------------------------------------------------------


def tool_scan_slop(args: dict[str, Any]) -> dict[str, Any]:
    """Scan a path with the AI-integrity engine (read-only)."""
    root = _resolve(str(args.get("path", ".")))
    max_score = int(args.get("max_score", 8))
    provenance = bool(args.get("provenance", True))
    phrases = detector.load_phrases()
    findings = detector.scan(root, phrases, provenance=provenance)

    per_file: dict[str, list] = {}
    for finding in findings:
        per_file.setdefault(finding.path, []).append(finding)
    deficits = [detector.file_deficit(items) for items in per_file.values()]
    score, grade, verdict = detector.project_score(deficits)
    blocking = [f for f in findings if not f.advisory]
    advisory = [f for f in findings if f.advisory]
    blocking_total = detector.blocking_score(findings)
    would_fail = blocking_total > max_score

    serialized = [
        {
            "path": f.path,
            "line": f.line,
            "check": f.check,
            "message": f.message,
            "weight": f.weight,
            "advisory": f.advisory,
            "remedy": f.remedy,
        }
        for f in (blocking + advisory)[:MAX_FINDINGS_IN_RESPONSE]
    ]
    return {
        "path": str(root),
        "score": score,
        "grade": grade,
        "verdict": verdict,
        "blocking_score": blocking_total,
        "blocking_gate": {"max_score": max_score, "would_fail_ci": would_fail},
        "advisory_score": detector.advisory_score(findings),
        "files_with_findings": len(per_file),
        "total_findings": len(findings),
        "truncated": len(findings) > MAX_FINDINGS_IN_RESPONSE,
        "findings": serialized,
        "note": "blocking findings gate CI above max_score; advisory never gate",
        "prompt": (
            f"AI-integrity scan of {root}: project score {score}/100 ({grade}, {verdict}), "
            f"blocking score {blocking_total} against the CI gate of {max_score} "
            f"({'WOULD FAIL CI' if would_fail else 'would pass CI'}). Work the findings "
            "top-down, each carries a remedy field. Mechanical cleanup: call apply_fixes "
            f'with {{"path": "{root}", "tiers": ["safe"]}}. Prose-level findings '
            "(meaning, tone, structure) must be rewritten deliberately, never by regex. "
            "Re-run this scan afterwards and confirm the blocking score dropped."
        ),
    }


def _changed_files(root: Path, base: str, include_uncommitted: bool) -> list[str]:
    """Changed paths for scan_diff: git diff against base, optionally vs HEAD."""
    paths: list[str] = []
    if base:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", base],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            raise ValueError(f"git diff failed: {out.stderr.strip()[:200]}")
        paths.extend(line for line in out.stdout.splitlines() if line)
    if include_uncommitted:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0:
            paths.extend(line for line in out.stdout.splitlines() if line)
    return sorted(set(paths))


def tool_scan_diff(args: dict[str, Any]) -> dict[str, Any]:
    """Scan only the changed files of a repository (read-only)."""
    root = _resolve(str(args.get("path", ".")))
    base = str(args.get("base", "")).strip()
    include_uncommitted = bool(args.get("include_uncommitted", not base))
    if not (root / ".git").exists():
        raise ValueError(f"not a git repository: {root}")
    changed = _changed_files(root, base, include_uncommitted)
    phrases = detector.load_phrases()
    findings = detector.scan(root, phrases)
    changed_set = set(changed)
    hits = [f for f in findings if f.path in changed_set]
    filters, source = path_filters.filters_for_repo(root)
    fired = path_filters.stacks_for_paths(changed, filters)
    blocking = detector.blocking_score(hits)
    label = base or "HEAD (uncommitted)"
    return {
        "path": str(root),
        "base": label,
        "changed_files": changed,
        "changed_count": len(changed),
        "stacks_fired": sorted(fired),
        "filter_source": source,
        "blocking_score": blocking,
        "findings": [
            {
                "path": f.path,
                "line": f.line,
                "check": f.check,
                "message": f.message,
                "weight": f.weight,
                "advisory": f.advisory,
                "remedy": f.remedy,
            }
            for f in hits[:MAX_FINDINGS_IN_RESPONSE]
        ],
        "prompt": (
            f"Change set on {root} vs {label}: {len(changed)} files, CI stacks "
            f"{sorted(fired)}, blocking score {blocking}. Review the findings above before "
            f"you build on this diff; preview the mechanical cleanup with plan_fixes "
            f'{{"path": "{root}"}}. Only changed files were scanned, the rest of the '
            "repository was left alone. Re-run this with the full file list before "
            "opening the PR."
        ),
    }


def _tier_list(args: dict[str, Any]) -> list[str]:
    raw = args.get("tiers", ["safe"])
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    tiers = [tier for tier in raw if tier in ("safe", "lint")]
    return tiers or ["safe"]


def tool_plan_fixes(args: dict[str, Any]) -> dict[str, Any]:
    """Dry-run the fix policy: what WOULD be fixed, nothing touched."""
    root = _resolve(str(args.get("path", ".")))
    tiers = _tier_list(args)
    semgrep_config = str(args.get("semgrep_config", ""))
    plan = fix_engine.build_plan(root, set(tiers), semgrep_config)
    generic = fix_engine.scan_generic(root, dry_run=True)
    tool_names = [entry["name"] for entry in plan]
    return {
        "path": str(root),
        "tiers": tiers,
        "planned_tools": [{"name": entry["name"], "tier": entry["tier"]} for entry in plan],
        "generic": generic,
        "note": "dry-run: no file was modified; use apply_fixes to execute",
        "prompt": (
            f"Fix plan for {root} (dry run, nothing modified): tools {tool_names} run in "
            f"that order and the normalizer would touch {generic['files_changed']} of "
            f"{generic['files_scanned']} files. Execute with apply_fixes "
            f'{{"path": "{root}", "tiers": {json.dumps(tiers)}}}, review every '
            "change with git diff, then re-run plan_fixes on the same path and expect "
            "zero files changed."
        ),
    }


def _snapshot(root: Path) -> dict[str, str]:
    """Hash every regular file under root for working-tree change detection."""
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in SNAPSHOT_SKIP_DIRS for part in rel_parts[:-1]):
            continue
        try:
            snapshot[path.relative_to(root).as_posix()] = hashlib.sha1(
                path.read_bytes()
            ).hexdigest()
        except OSError:
            continue
    return snapshot


def tool_apply_fixes(args: dict[str, Any]) -> dict[str, Any]:
    """Run the fix policy for real (MUTATES files under path)."""
    root = _resolve(str(args.get("path", ".")))
    tiers = _tier_list(args)
    semgrep_config = str(args.get("semgrep_config", ""))
    plan = fix_engine.build_plan(root, set(tiers), semgrep_config)
    results: list[dict[str, Any]] = []
    generic: dict[str, Any] = {"files_scanned": 0, "files_changed": 0, "skipped": {}}
    before = _snapshot(root)
    with tempfile.TemporaryDirectory(prefix="master-ci-mcp-") as tmp:
        for entry in plan:
            name = entry["name"]
            if name == "generic":
                generic = fix_engine.scan_generic(root, dry_run=False)
                results.append(
                    {
                        "name": "generic",
                        "tier": "safe",
                        "status": "fixed" if generic["files_changed"] else "clean",
                        "detail": (
                            f"{generic['files_changed']} of "
                            f"{generic['files_scanned']} files normalized"
                        ),
                    }
                )
            elif name == "semgrep":
                status, detail = fix_engine.tool_semgrep(root, semgrep_config, False)
                results.append({"name": name, "tier": "lint", "status": status, "detail": detail})
            else:
                status, detail = entry["runner"](root, dry_run=False)
                results.append(
                    {"name": name, "tier": entry["tier"], "status": status, "detail": detail}
                )
        fix_engine.write_reports(
            Path(tmp), root, sorted(set(tiers)), semgrep_config, results, generic, False
        )
        summary = (Path(tmp) / "summary.md").read_text(encoding="utf-8")
    after = _snapshot(root)
    changed_files = sorted(
        key for key in set(before) | set(after) if before.get(key) != after.get(key)
    )
    changed_any = bool(changed_files)
    if changed_any:
        follow_up = (
            f"Fixes applied to {root}: {len(changed_files)} file(s) changed, including "
            f"{', '.join(changed_files[:10])}. Review every change with git diff before "
            "committing and drop anything you dislike (git checkout -- <file>). Verify "
            "by re-running plan_fixes on the same path: it must now report zero files "
            "changed. Re-run scan_slop to confirm the score moved too."
        )
    else:
        follow_up = (
            f"Nothing needed fixing in {root}: no file changed. No action required; "
            "re-run plan_fixes whenever the code changes."
        )
    return {
        "path": str(root),
        "tiers": sorted(set(tiers)),
        "changed_any": changed_any,
        "changed_files": changed_files[:SNAPSHOT_MAX_CHANGED],
        "changed_files_truncated": len(changed_files) > SNAPSHOT_MAX_CHANGED,
        "tools": results,
        "generic": generic,
        "summary": summary,
        "note": (
            "files were modified in place; the engine statuses mean the tool ran ok, "
            "changed_any and changed_files are measured from the working tree itself"
        ),
        "prompt": follow_up,
    }


def tool_list_checks(_args: dict[str, Any]) -> dict[str, Any]:
    """The check catalog: detector rules, remedies, fix policy, scoring."""
    phrases = detector.load_phrases()
    return {
        "detector": {
            "blocking_checks": sorted(detector.BLOCKING_CHECKS),
            "phrase_categories": {name: len(entries) for name, entries in phrases.items()},
            "phrases": phrases,
            "remedies": dict(detector.REMEDIES),
            "scoring": "project = min(100, 0.6*worst_file_deficit + 0.4*mean_file_deficit)",
            "advisory_note": "provenance and prose findings never gate CI",
        },
        "fix_policy": {
            "safe_tier": [
                "ruff format",
                "rustfmt",
                "gofmt",
                "biome",
                "shfmt",
                "markdownlint --fix",
                "generic whitespace/EOF normalizer",
            ],
            "lint_tier": [
                "ruff check --fix",
                "go mod tidy",
                "sqlfluff fix",
                "semgrep --autofix",
            ],
            "never_auto_fixed": [
                "secrets (rotate)",
                "dependency vulnerabilities (upgrade decisions)",
                "AI-integrity prose findings (meaning)",
            ],
        },
        "ci_catalog": "docs/checks.md in the master-ci repository (80+ checks)",
        "prompt": (
            "Catalog reference only. For a concrete next step, run scan_slop on your "
            "target path and act on each finding's remedy field; plan_fixes previews "
            "what apply_fixes would change mechanically."
        ),
    }


def tool_filter_paths(args: dict[str, Any]) -> dict[str, Any]:
    """Which CI stacks would fire for a change set (uses the live table)."""
    raw = args.get("files", [])
    if isinstance(raw, str):
        raw = [line.strip() for line in raw.splitlines() if line.strip()]
    files = [str(f) for f in raw]
    if not files:
        raise ValueError("files must be a non-empty list of repo-relative paths")
    root_arg = args.get("path")
    root = _resolve(str(root_arg)) if root_arg else Path.cwd()
    filters, source = path_filters.filters_for_repo(root)
    fired = path_filters.stacks_for_paths(files, filters)
    detail = {
        path: sorted(name for name, fs in filters.items() if fs.matches(path)) for path in files
    }
    preview = ", ".join(files[:10]) + ("..." if len(files) > 10 else "")
    return {
        "fired": sorted(fired),
        "idle": sorted(set(filters) - fired),
        "source": source,
        "detail": detail,
        "note": "source=live reads .github/workflows/master-ci.yml; fallback uses the committed snapshot",
        "prompt": (
            f"Change set preview: {preview}. CI would run these stacks: {sorted(fired)} "
            f"and skip these: {sorted(set(filters) - fired)}. Re-run filter_paths with "
            "the full file list before opening the PR, and pair it with scan_diff to "
            "score the changed files."
        ),
    }


def tool_diagnose(_args: dict[str, Any]) -> dict[str, Any]:
    """Environment report: what this install can and cannot do right now."""
    available = {name: shutil.which(name) for name in EXTERNAL_TOOLS}
    missing = sorted(name for name, where in available.items() if where is None)
    lock = os.environ.get("MASTER_CI_MCP_ROOT")
    self_test = f"python3 {SCRIPTS / 'mcp_ping.py'}"
    missing_text = f"{len(missing)} ({', '.join(missing)})" if missing else "none"
    return {
        "server": {
            "name": "master-ci",
            "version": SERVER_VERSION,
            "protocol_versions": list(PROTOCOL_VERSIONS),
        },
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "server_root": str(SCRIPTS),
        "repo_root": str(REPO_ROOT),
        "cwd": str(Path.cwd()),
        "path_lock": lock or "(unset: tool paths may target anything)",
        "engines_loaded": ["ai_slop_detector", "fix_engine", "path_filters"],
        "external_tools": available,
        "missing_tools": missing,
        "mutating_tools": ["apply_fixes"],
        "prompt": (
            f"Install verified: master-ci v{SERVER_VERSION} on Python "
            f"{sys.version.split()[0]}, missing formatters: {missing_text}. Missing "
            "tools only narrow apply_fixes; the scan tools are stdlib and always work. "
            f"For a full wire-level self test run: {self_test}."
        ),
    }


def _version_tuple(version: str) -> tuple[int, ...]:
    """Parse 'v1.2.3' into (1, 2, 3); unparsable chunks stop the parse."""
    chunks: list[int] = []
    for chunk in version.lstrip("v").split("."):
        if chunk.isdigit():
            chunks.append(int(chunk))
        else:
            break
    return tuple(chunks)


def _newest_tag(names: list[str]) -> str | None:
    """Highest semver tag among the names, or None when none parses."""
    candidates = [(_version_tuple(name), name) for name in names if _version_tuple(name)]
    if not candidates:
        return None
    return max(candidates)[1]


def _git_sha() -> str | None:
    """Short HEAD sha of the install checkout, when it is a git repo."""
    if not (REPO_ROOT / ".git").exists():
        return None
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip()[:12] or None


def _update_command(slug: str) -> str:
    """The command a user runs to pull the newest release, by install type."""
    if (REPO_ROOT / ".git").exists():
        return f"git -C {REPO_ROOT} pull --ff-only && git -C {REPO_ROOT} fetch --tags --force"
    return f"git clone --depth 1 https://github.com/{slug} master-ci"


def _fetch_latest_tag(slug: str, api: str, timeout: float) -> str | None:
    """Latest semver tag from the GitHub API; raises on network failure."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "master-ci-mcp"}
    token = os.environ.get("MASTER_CI_GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{api}/repos/{slug}/tags?per_page=100", headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        tags = json.loads(response.read().decode("utf-8"))
    names = [str(tag.get("name", "")) for tag in tags if isinstance(tag, dict)]
    return _newest_tag(names)


def tool_bench(args: dict[str, Any]) -> dict[str, Any]:
    """Measure engine throughput and latency on a deterministic corpus."""
    bench_module = _load("benchmark")

    scale = str(args.get("scale", "small"))
    if scale not in bench_module.SCALES:
        raise ValueError(f"scale must be one of: {', '.join(sorted(bench_module.SCALES))}")
    repeat = int(args.get("repeat", 1))
    metrics = bench_module.run_suite(scale=scale, repeat=max(1, repeat), with_mcp=False)
    return {
        "scale": scale,
        "repeat": repeat,
        "metrics": metrics,
        "units": bench_module.UNITS,
        "note": (
            "MCP roundtrips are excluded here (this call already runs inside the "
            "server); run scripts/benchmark.py for the full suite including MCP "
            "startup and stdio roundtrips."
        ),
        "prompt": (
            f"Benchmark finished on the {scale} corpus: detector "
            f"{metrics.get('detector_files_per_sec', 0):.0f} files/s, fix plan "
            f"{metrics.get('fix_plan_ms', 0):.0f} ms, generic scan "
            f"{metrics.get('fix_generic_scan_ms', 0):.0f} ms, path filters "
            f"{metrics.get('pathfilter_paths_per_sec', 0):.0f} paths/s. To gate a "
            "release on performance, run: python3 scripts/benchmark.py --save "
            "baseline.json, then benchmark.py --check baseline.json before the next "
            "tag. Pair with diagnose to see which external tools are missing on "
            "this machine."
        ),
    }


def tool_check_updates(_args: dict[str, Any]) -> dict[str, Any]:
    """Compare the installed version against the newest tag on GitHub."""
    slug = os.environ.get("MASTER_CI_REPO", DEFAULT_REPO_SLUG)
    api = os.environ.get("MASTER_CI_GH_API", DEFAULT_GH_API)
    latest_tag: str | None = None
    error: str | None = None
    try:
        latest_tag = _fetch_latest_tag(slug, api, UPDATE_TIMEOUT_SECONDS)
        if latest_tag is None:
            error = f"no semver tags found on https://github.com/{slug}"
    except Exception as exc:  # noqa: BLE001 - offline is a normal state, not a crash
        error = f"{type(exc).__name__}: {exc}"

    payload: dict[str, Any] = {
        "repo": slug,
        "installed_version": SERVER_VERSION,
        "installed_sha": _git_sha(),
        "auto_update": False,
        "update_command": _update_command(slug),
    }
    if latest_tag is None:
        payload["error"] = error
        payload["prompt"] = (
            f"Update check could not run: {error}. The install stays fully usable; "
            f"compare versions manually at https://github.com/{slug}/releases, or "
            "re-run check_updates once the network is back. Nothing ever updates "
            "itself."
        )
        return payload
    payload["latest_tag"] = latest_tag
    payload["up_to_date"] = _version_tuple(latest_tag) <= _version_tuple(SERVER_VERSION)
    if payload["up_to_date"]:
        payload["prompt"] = (
            f"Install is current: v{SERVER_VERSION} matches {latest_tag}. Nothing to "
            "do; re-run check_updates occasionally as part of a diagnose review."
        )
    else:
        payload["prompt"] = (
            f"Update available: installed v{SERVER_VERSION}, latest tag {latest_tag}. "
            f"Review the release notes, then run: {_update_command(slug)}. Updates are "
            "never pushed to your device automatically - you pull when you choose to."
        )
    return payload


# ---------------------------------------------------------------------------
# tool metadata: names, schemas, descriptions (MCP tools/list)
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "scan_slop",
        "description": (
            "Scan a file or directory with the Master CI AI-integrity engine. "
            "Returns the project slop score (0-100 with grade and verdict), the "
            "blocking score that CI gates on, and every finding with its "
            "machine-applicable remedy. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "file or directory to scan"},
                "max_score": {"type": "integer", "description": "CI gate threshold", "default": 8},
                "provenance": {
                    "type": "boolean",
                    "description": "include AI-provenance findings",
                    "default": True,
                },
            },
            "required": [],
        },
    },
    {
        "name": "scan_diff",
        "description": (
            "Scan only the changed files of a git repository: uncommitted "
            "changes by default, or a diff against a base ref. Also reports "
            "which CI stacks the change set activates. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "repository root"},
                "base": {
                    "type": "string",
                    "description": "git ref to diff against (default: uncommitted vs HEAD)",
                },
                "include_uncommitted": {"type": "boolean", "default": True},
            },
            "required": [],
        },
    },
    {
        "name": "plan_fixes",
        "description": (
            "Dry-run of the Master CI fix policy for a repository: which "
            "tools would run (safe and lint tiers) and how many files the "
            "whitespace normalizer would touch. Nothing is modified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "repository root"},
                "tiers": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["safe", "lint"]},
                    "default": ["safe"],
                },
                "semgrep_config": {"type": "string", "default": ""},
            },
            "required": [],
        },
    },
    {
        "name": "apply_fixes",
        "description": (
            "MUTATES FILES. Runs the Master CI fix policy in place: safe tier "
            "(formatters, whitespace normalizer) and optionally the lint tier "
            "(tool-verified transforms). Review the result with git diff. The "
            "summary mirrors exactly what the autofix/self-heal loop would do."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "repository root"},
                "tiers": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["safe", "lint"]},
                    "default": ["safe"],
                },
                "semgrep_config": {"type": "string", "default": ""},
            },
            "required": [],
        },
    },
    {
        "name": "list_checks",
        "description": (
            "The Master CI check catalog from the server's engines: blocking "
            "vs advisory detector rules, the phrase database, per-finding "
            "remedies, the scoring formula, and the fix policy tiers."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "filter_paths",
        "description": (
            "Ask which CI stacks a change set would activate before you write "
            "the code: uses the live dorny/paths-filter table from "
            ".github/workflows/master-ci.yml (or the committed snapshot). "
            "Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "repo-relative changed paths",
                },
                "path": {"type": "string", "description": "repository root (default: server cwd)"},
            },
            "required": ["files"],
        },
    },
    {
        "name": "diagnose",
        "description": (
            "Environment report for this install: server and protocol versions, "
            "the loaded engines, which external formatters are on PATH and which "
            "are missing, the active path lock, and the one-command self test. "
            "Read-only."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "bench",
        "description": (
            "Run the performance benchmark of the Master CI engines (AI-integrity "
            "detector, fix engine, path filters) against a deterministic synthetic "
            "corpus and get comparable numbers for this machine. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scale": {
                    "type": "string",
                    "enum": ["small", "full"],
                    "description": "corpus size (default: small for quick checks)",
                },
                "repeat": {
                    "type": "integer",
                    "description": "best-of-N timing runs (default 1)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "check_updates",
        "description": (
            "Compare the installed master-ci version against the newest tag on "
            "GitHub and return the exact update command. The server never "
            "updates itself; network is only touched by this tool and offline "
            "is reported gracefully."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]

TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "scan_slop": tool_scan_slop,
    "scan_diff": tool_scan_diff,
    "plan_fixes": tool_plan_fixes,
    "apply_fixes": tool_apply_fixes,
    "list_checks": tool_list_checks,
    "filter_paths": tool_filter_paths,
    "diagnose": tool_diagnose,
    "check_updates": tool_check_updates,
}


# ---------------------------------------------------------------------------
# MCP prompts: ready-made walkthroughs (prompts/list, prompts/get)
# ---------------------------------------------------------------------------


def _prompt_fix_my_repo(args: dict[str, Any]) -> str:
    """The full fix loop as one paste-ready instruction."""
    root = _resolve(str(args.get("path", ".")))
    tiers_json = json.dumps(_tier_list(args))
    return (
        f"Improve the repository at {root} with the master-ci tools, in this order. "
        f'1. scan_slop {{"path": "{root}"}}: read the score, blocking_score and '
        "findings. 2. Fix prose-level findings by editing deliberately; meaning-level "
        "rewrites are never mechanical. 3. plan_fixes "
        f'{{"path": "{root}", "tiers": {tiers_json}}}: preview the mechanical '
        f'cleanup. 4. apply_fixes {{"path": "{root}", "tiers": {tiers_json}}}: '
        "execute it. 5. Re-run scan_slop and plan_fixes: the blocking score should drop "
        "and the plan should report zero files changed. 6. Show git diff --stat and "
        "summarize what changed and why."
    )


def _prompt_explain_findings(args: dict[str, Any]) -> str:
    """Reviewer-style explanation prompt for a scan."""
    root = _resolve(str(args.get("path", ".")))
    return (
        f"Explain the master-ci scan of {root} like a code reviewer. Call scan_slop "
        f'with {{"path": "{root}"}}, then: summarize what the score and grade mean '
        "for this repository; group the findings by check; say which are mechanical "
        "(fixable by apply_fixes) and which are meaning-level and need real edits; "
        "propose the exact apply_fixes call you would make; state what CI would do with "
        "the current blocking score."
    )


def _prompt_pre_flight(args: dict[str, Any]) -> str:
    """Pre-push check: which CI stacks fire and how the diff scores."""
    raw = args.get("files", [])
    if isinstance(raw, str):
        raw = [line.strip() for line in raw.splitlines() if line.strip()]
    files = [str(item) for item in raw]
    if not files:
        raise ValueError("files must be a non-empty list of repo-relative paths")
    root_arg = args.get("path")
    root = _resolve(str(root_arg)) if root_arg else Path.cwd()
    files_json = json.dumps(files[:20])
    filter_call = f'filter_paths {{"files": {files_json}, "path": "{root}"}}'
    diff_call = f'scan_diff {{"path": "{root}"}}'
    return (
        f"Pre-flight check before pushing changes in {root}. Call {filter_call} to see "
        f"which CI stacks fire, then call {diff_call} to score the changed files. "
        "Report which checks CI will run, the blocking score, and what to fix before "
        "opening the PR."
    )


PROMPTS: list[dict[str, Any]] = [
    {
        "name": "fix-my-repo",
        "description": "Fix-ready walkthrough: scan, plan, apply, verify, report.",
        "arguments": [
            {
                "name": "path",
                "description": "repository root (default: server cwd)",
                "required": False,
            },
            {
                "name": "tiers",
                "description": "fix tiers, safe (default) or safe and lint",
                "required": False,
            },
        ],
    },
    {
        "name": "explain-findings",
        "description": (
            "Reviewer-style explanation of a scan: what is mechanical, what needs real edits."
        ),
        "arguments": [
            {
                "name": "path",
                "description": "repository root (default: server cwd)",
                "required": False,
            },
        ],
    },
    {
        "name": "pre-flight",
        "description": "Before pushing: which CI stacks fire and how the changed files score.",
        "arguments": [
            {
                "name": "files",
                "description": "repo-relative changed paths (list or newline text)",
                "required": True,
            },
            {
                "name": "path",
                "description": "repository root (default: server cwd)",
                "required": False,
            },
        ],
    },
]

PROMPT_BUILDERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "fix-my-repo": _prompt_fix_my_repo,
    "explain-findings": _prompt_explain_findings,
    "pre-flight": _prompt_pre_flight,
}


# ---------------------------------------------------------------------------
# JSON-RPC plumbing (MCP stdio: one message per line)
# ---------------------------------------------------------------------------


def _write(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _result(req_id: Any, payload: dict[str, Any]) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "result": payload})


def _error(req_id: Any, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _tool_result(req_id: Any, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2)
    _result(req_id, {"content": [{"type": "text", "text": text}], "isError": False})


def _tool_error(req_id: Any, message: str) -> None:
    _result(req_id, {"content": [{"type": "text", "text": message}], "isError": True})


def handle_request(msg: dict[str, Any]) -> None:
    """Dispatch one JSON-RPC request or notification."""
    method = str(msg.get("method", ""))
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if req_id is None:  # notification: never responded to
        return

    if method == "initialize":
        requested = str(params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION))
        agreed = requested if requested in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
        _result(
            req_id,
            {
                "protocolVersion": agreed,
                "capabilities": {"tools": {}, "prompts": {}},
                "serverInfo": {"name": "master-ci", "version": SERVER_VERSION},
                "instructions": (
                    "Call tools/list to discover the engines. Every tool result embeds "
                    "a fix-ready prompt field with the next step; prompts/list serves "
                    "ready made walkthroughs (fix-my-repo, explain-findings, "
                    "pre-flight). Set MASTER_CI_MCP_ROOT in the env block to pin this "
                    "server to one repository."
                ),
            },
        )
    elif method == "ping":
        _result(req_id, {})
    elif method == "tools/list":
        _result(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        name = str(params.get("name", ""))
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            _error(req_id, -32602, f"unknown tool: {name}")
            return
        try:
            _tool_result(req_id, handler(dict(params.get("arguments") or {})))
        except Exception as exc:  # noqa: BLE001 - tool failures are results, not crashes
            _tool_error(req_id, f"{type(exc).__name__}: {exc}")
    elif method == "prompts/list":
        _result(req_id, {"prompts": PROMPTS})
    elif method == "prompts/get":
        prompt_name = str(params.get("name", ""))
        builder = PROMPT_BUILDERS.get(prompt_name)
        if builder is None:
            _error(req_id, -32602, f"unknown prompt: {prompt_name}")
            return
        try:
            text = builder(dict(params.get("arguments") or {}))
        except Exception as exc:  # noqa: BLE001 - bad prompt args are client errors
            _error(req_id, -32602, f"invalid prompt arguments: {type(exc).__name__}: {exc}")
            return
        description = next(p["description"] for p in PROMPTS if p["name"] == prompt_name)
        _result(
            req_id,
            {
                "description": description,
                "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
            },
        )
    elif method == "resources/list":
        _result(req_id, {"resources": []})
    else:
        _error(req_id, -32601, f"method not found: {method}")


def serve(stdin_reader=sys.stdin) -> int:
    """Main loop: read JSON-RPC lines until EOF."""
    print(
        f"[master-ci-mcp] v{SERVER_VERSION} ready: {len(TOOLS)} tools, "
        "protocol " + ", ".join(PROTOCOL_VERSIONS),
        file=sys.stderr,
    )
    for line in stdin_reader:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _error(None, -32700, "parse error: line is not valid JSON")
            continue
        if not isinstance(msg, dict):
            _error(None, -32600, "invalid request: expected an object")
            continue
        try:
            handle_request(msg)
        except Exception as exc:  # noqa: BLE001 - the server must never die on a message
            req_id = msg.get("id")
            if req_id is not None:
                _error(req_id, -32603, f"internal error: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(serve())
