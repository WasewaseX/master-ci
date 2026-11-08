"""MCP tool implementations: scan_slop, scan_diff, plan_fixes, apply_fixes
and the path/root resolution they share."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parent
SERVER_VERSION = "1.12.0"
DEFAULT_REPO_SLUG = "WasewaseX/master-ci"
DEFAULT_GH_API = "https://api.github.com"
UPDATE_TIMEOUT_SECONDS = 10.0
PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
    "2026-07-28",
)
MODERN_PROTOCOL_VERSION = "2026-07-28"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
UNSUPPORTED_PROTOCOL_VERSION_ERROR = -32022
META_PROTOCOL_KEY = "io.modelcontextprotocol/protocolVersion"
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


def _resolve(path: str) -> Path:
    """Resolve a tool path argument and enforce the root lock when configured."""
    root_lock = os.environ.get("MASTER_CI_MCP_ROOT")
    resolved = Path(path).expanduser().resolve()
    if root_lock:
        locked = Path(root_lock).expanduser().resolve()
        if resolved != locked and locked not in resolved.parents:
            raise ValueError(f"path {resolved} is outside MASTER_CI_MCP_ROOT ({locked})")
    if not resolved.exists():
        raise ValueError(f"path does not exist: {resolved}")
    return resolved


def _require_write_root() -> Path:
    """Mandatory root lock for mutating tools - reads stay flexible, writes don't.

    Without this gate a misconfigured client could point apply_fixes at any
    directory the server process can reach. Enforced, not recommended: the
    tool simply refuses to mutate anything until the operator declares the
    one repository this server is allowed to touch.
    """
    root_lock = os.environ.get("MASTER_CI_MCP_ROOT")
    if not root_lock or not root_lock.strip():
        raise ValueError(
            "apply_fixes refused: MASTER_CI_MCP_ROOT is not set. Mutating tools "
            "require an explicit repository root - add it to the env block of "
            'your mcpServers config ("env": {"MASTER_CI_MCP_ROOT": "/abs/path"}) '
            "and reconnect."
        )
    return Path(root_lock).expanduser().resolve()


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
    """Changed paths for scan_diff: git diff against base, optionally vs HEAD.

    Untracked files are discovered with `git ls-files --others` - plain
    `git diff` never reports them, and a brand new source file is exactly
    the change an AI assistant most needs the pre-flight scan to see.
    Ignored files stay invisible (respecting .gitignore), matching what a
    commit would actually contain.
    """
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
    untracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
    )
    if untracked.returncode == 0:
        paths.extend(line for line in untracked.stdout.splitlines() if line)
    return sorted(set(paths))


def _finding_rel_path(finding_path: str, root: Path) -> str | None:
    """Normalize a detector finding path to a repo-relative posix path.

    The detector reports absolute paths for directory scans; some rule
    helpers already emit repo-relative ones. scan_diff intersects findings
    with the git change set, so both shapes must land in the same space.
    """
    candidate = Path(finding_path)
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(root).as_posix()
    except ValueError:
        return None


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
    hits = []
    for f in findings:
        rel = _finding_rel_path(f.path, root)
        if rel is not None and rel in changed_set:
            hits.append((rel, f))
    filters, source = path_filters.filters_for_repo(root)
    fired = path_filters.stacks_for_paths(changed, filters)
    blocking = detector.blocking_score([f for _, f in hits])
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
                "path": rel,
                "line": f.line,
                "check": f.check,
                "message": f.message,
                "weight": f.weight,
                "advisory": f.advisory,
                "remedy": f.remedy,
            }
            for rel, f in hits[:MAX_FINDINGS_IN_RESPONSE]
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
            snapshot[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        except OSError:
            continue
    return snapshot


def _run_fix_plan(
    root: Path,
    plan: list[dict[str, Any]],
    semgrep_config: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Execute every planned tool for real; returns the generic-normalizer stats."""
    generic: dict[str, Any] = {"files_scanned": 0, "files_changed": 0, "skipped": {}}
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
                        f"{generic['files_changed']} of {generic['files_scanned']} files normalized"
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
    return generic


def tool_apply_fixes(args: dict[str, Any]) -> dict[str, Any]:
    """Run the fix policy for real (MUTATES files under path)."""
    write_root = _require_write_root()  # mandatory: no lock, no mutation
    root = _resolve(str(args.get("path", ".")))
    if root != write_root and write_root not in root.parents:
        raise ValueError(f"path {root} is outside MASTER_CI_MCP_ROOT ({write_root})")
    tiers = _tier_list(args)
    semgrep_config = str(args.get("semgrep_config", ""))
    plan = fix_engine.build_plan(root, set(tiers), semgrep_config)
    results: list[dict[str, Any]] = []
    generic: dict[str, Any] = {"files_scanned": 0, "files_changed": 0, "skipped": {}}
    before = _snapshot(root)
    with tempfile.TemporaryDirectory(prefix="master-ci-mcp-") as tmp:
        generic = _run_fix_plan(root, plan, semgrep_config, results)
        fix_engine.write_reports(
            fix_engine.ReportSpec(
                manifest_dir=Path(tmp),
                root=root,
                tiers=sorted(set(tiers)),
                semgrep_config=semgrep_config,
                results=results,
                generic_stats=generic,
                dry_run=False,
            )
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
