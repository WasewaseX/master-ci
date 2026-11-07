"""MCP meta tools (list_checks, filter_paths, diagnose, bench,
check_updates) and the prompt templates."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from mcp_tools import (
    DEFAULT_GH_API,
    DEFAULT_REPO_SLUG,
    EXTERNAL_TOOLS,
    PROTOCOL_VERSIONS,
    REPO_ROOT,
    SCRIPTS,
    SERVER_VERSION,
    UPDATE_TIMEOUT_SECONDS,
    _load,
    _resolve,
    detector,
    path_filters,
)


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
    lock_state = (
        str(lock) if lock else "(unset: apply_fixes is refused until MASTER_CI_MCP_ROOT is set)"
    )
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
        "path_lock": lock_state,
        "engines_loaded": ["ai_slop_detector", "fix_engine", "path_filters"],
        "external_tools": available,
        "missing_tools": missing,
        "mutating_tools": ["apply_fixes"],
        "prompt": (
            f"Install verified: master-ci v{SERVER_VERSION} on Python "
            f"{sys.version.split()[0]}, missing formatters: {missing_text}. Missing "
            "tools only narrow apply_fixes; the scan tools are stdlib and always work. "
            f"For a full wire-level self test run: {self_test}. apply_fixes requires "
            "MASTER_CI_MCP_ROOT to be set."
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
