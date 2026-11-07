#!/usr/bin/env python3
"""fix_engine.py - the single fix policy behind every master-ci auto-fix loop.

One source of truth for WHAT gets auto-fixed and HOW. It is shared by the
autofix workflow (weekly drift sweep plus the post-audit "find -> fix"
response) and the heal workflow (reactive response to a red default
branch). Because CI and the fix engine share the same tool set, a fix can
never fight its own check (no fix/check oscillation): the engine only
applies fixes for findings whose checks the same tools then re-verify.

The safe tier is pure formatting and whitespace normalization and cannot
change program behaviour: rustfmt, gofmt, ``ruff format``, Biome safe
fixes, shfmt, markdownlint --fix, and the generic trailing-whitespace /
final-newline normalizer. The lint tier applies tool-verified code
transforms: ``ruff check --fix``, ``go mod tidy``, ``sqlfluff fix``, and
``semgrep --autofix`` (only for rules that ship a machine-applicable fix).
These tools only change code when their own analysis says the change is
equivalent.

Never auto-fixed (needs a human; the audit issue tracks them instead):
secrets (rotate), dependency vulnerabilities (upgrade decisions),
AI-integrity prose findings (rewriting meaning is not mechanical),
linter findings without a shipped machine fix.

The engine is stdlib-only. External tools are invoked when present;
anything missing is recorded as ``skipped:tool-missing`` in the manifest,
never fatal: a fix run must not fail because a fixer was unavailable.

Exit code is always 0 unless the engine itself is misconfigured (2).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import sys
from pathlib import Path

from fix_tools import (
    find_files,
    has_go,
    has_node,
    has_python,
    has_rust,
    has_shell,
    has_sql,
    tool_biome,
    tool_go_mod_tidy,
    tool_gofmt,
    tool_markdownlint,
    tool_ruff_fix,
    tool_ruff_format,
    tool_rustfmt,
    tool_semgrep,
    tool_shfmt,
    tool_sqlfluff,
)

# Version-pinned shfmt, fetched only when *.sh files exist and no local
# shfmt is on PATH (same pattern the deep audit uses for osv-scanner).


MAX_TEXT_BYTES = 2_000_000  # never rewrite files larger than 2 MB


# ---------------------------------------------------------------------------
# file discovery - git-aware with a non-git fallback for tests
# ---------------------------------------------------------------------------


def _safe_read(path: Path, limit: int) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read(limit)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# the generic normalizer - trailing whitespace and final newline
# ---------------------------------------------------------------------------


def _fix_text(content: str, suffix: str) -> str:
    """Strip trailing whitespace and normalize the final newline.

    Markdown exception: a run of exactly two trailing spaces is a hard
    line break (CommonMark), and markdownlint MD009 allows it by default.
    Preserve it; strip longer runs and everything else.
    """
    is_markdown = suffix in {".md", ".markdown"}

    def strip_line(line: str) -> str:
        stripped = line.rstrip(" \t")
        if is_markdown and line.endswith("  ") and not line.endswith("   "):
            stripped += "  "
        return stripped

    lines = content.split("\n")
    fixed = [strip_line(line) for line in lines]
    # collapse every trailing blank line down to exactly one final newline
    while fixed and fixed[-1] == "":
        fixed.pop()
    text = "\n".join(fixed)
    return text + "\n" if text else ""


def normalize_file(path: Path) -> tuple[bool, str]:
    """Return (changed, reason). Never raises: unreadable/binary -> skip."""
    raw = _safe_read(path, MAX_TEXT_BYTES)
    if raw is None or not raw:
        return False, "empty-or-unreadable"
    if b"\0" in raw[:8192]:
        return False, "binary"
    if len(raw) >= MAX_TEXT_BYTES:
        return False, "too-large"
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False, "non-utf8"
    fixed = _fix_text(content, path.suffix)
    if fixed == content:
        return False, "already-clean"
    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(fixed)
    except OSError as exc:
        return False, f"write-failed:{exc.__class__.__name__}"
    return True, "normalized"


def scan_generic(root: Path, dry_run: bool) -> dict:
    """Normalize every text file, or only count when dry_run."""
    scanned = 0
    changed = 0
    skipped: dict[str, int] = {}
    for name in find_files(root, lambda p: True):
        path = root / name
        raw = _safe_read(path, MAX_TEXT_BYTES)
        if raw is None or not raw or b"\0" in raw[:8192] or len(raw) >= MAX_TEXT_BYTES:
            continue
        try:
            content = raw.decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if dry_run:
            scanned += 1
            if _fix_text(content, path.suffix) != content:
                changed += 1
            continue
        ok, reason = normalize_file(path)
        if ok:
            changed += 1
            scanned += 1
        elif reason == "already-clean":
            scanned += 1
        else:
            skipped[reason] = skipped.get(reason, 0) + 1
    return {"files_scanned": scanned, "files_changed": changed, "skipped": skipped}


# ---------------------------------------------------------------------------
# tool runners - each returns (status, detail)
# status in {fixed, clean, skipped:tool-missing, failed, planned}
# ---------------------------------------------------------------------------


def build_plan(root: Path, tiers: set[str], semgrep_config: str) -> list[dict]:
    plan: list[dict] = []

    def add(name: str, tier: str, active: bool, runner) -> None:
        if tier in tiers and active:
            plan.append({"name": name, "tier": tier, "runner": runner})

    add("ruff-format", "safe", has_python(root), tool_ruff_format)
    add("rustfmt", "safe", has_rust(root), tool_rustfmt)
    add("gofmt", "safe", has_go(root), tool_gofmt)
    add("biome", "safe", has_node(root), tool_biome)
    add("shfmt", "safe", has_shell(root), tool_shfmt)
    add("markdownlint", "safe", True, tool_markdownlint)
    add("ruff-fix", "lint", has_python(root), tool_ruff_fix)
    add("go-mod-tidy", "lint", has_go(root), tool_go_mod_tidy)
    add("sqlfluff", "lint", has_sql(root), tool_sqlfluff)
    add("semgrep", "lint", bool(semgrep_config), None)
    add("generic", "safe", True, None)  # in-engine normalizer, always last
    return plan


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def _summary_row(r: dict, generic_stats: dict, dry_run: bool) -> str:
    if r["name"] == "generic":
        verb = "would be normalized" if dry_run else "normalized"
        gen = generic_stats
        return (
            f"| generic | safe | {r['status']} | {gen['files_changed']} of "
            f"{gen['files_scanned']} files {verb} |"
        )
    return f"| {r['name']} | {r['tier']} | {r['status']} | {r['detail']} |"


@dataclasses.dataclass(frozen=True)
class ReportSpec:
    """Everything the fix-engine reports need, resolved once in main."""

    manifest_dir: Path
    root: Path
    tiers: list[str]
    semgrep_config: str
    results: list[dict]
    generic_stats: dict
    dry_run: bool


def write_reports(spec: ReportSpec) -> None:
    manifest_dir = spec.manifest_dir
    root = spec.root
    tiers = spec.tiers
    semgrep_config = spec.semgrep_config
    results = spec.results
    generic_stats = spec.generic_stats
    dry_run = spec.dry_run
    manifest_dir.mkdir(parents=True, exist_ok=True)
    mode = "dry-run plan" if dry_run else "applied fixes"
    manifest = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": mode,
        "root": str(root),
        "tiers": tiers,
        "semgrep_config": semgrep_config or None,
        "tools": results,
        "generic": generic_stats,
    }
    (manifest_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    header = "| tool | tier | status | detail |\n| --- | --- | --- | --- |"
    rows = "\n".join(_summary_row(r, generic_stats, dry_run) for r in results)
    table = f"{header}\n{rows}"
    never_note = (
        "Never auto-fixed (tracked in the audit issue instead, they need a human): "
        "secrets (rotate them), dependency vulnerabilities (upgrade decisions), "
        "AI-integrity prose findings (rewriting meaning is not mechanical)."
    )

    summary = f"# Fix engine report - {mode}\n\n{table}\n\n{never_note}\n"
    (manifest_dir / "summary.md").write_text(summary, encoding="utf-8")

    pr_body = (
        "Automated fixes from the master-ci fix engine.\n\n"
        f"{table}\n\n"
        "Safety rails:\n"
        "- Nothing is pushed directly: this PR is the whole change.\n"
        "- Master CI re-runs on this branch; only merge with green CI.\n"
        "- Close this PR to roll back, or reopen it any time.\n\n"
        f"{never_note}\n"
    )
    (manifest_dir / "pr-body.md").write_text(pr_body, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="master-ci unified fix engine")
    parser.add_argument("--root", default=".", help="repository root (default: cwd)")
    parser.add_argument(
        "--tier",
        action="append",
        choices=["safe", "lint"],
        default=None,
        help="fix tier to apply (repeatable; default: safe)",
    )
    parser.add_argument(
        "--semgrep-config",
        default="",
        help="space-separated semgrep configs; enables semgrep --autofix",
    )
    parser.add_argument(
        "--manifest-dir", default="fix", help="where reports are written (default: fix/)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan without changing anything"
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        parser.error(f"--root {root} is not a directory")
    tiers = set(args.tier) if args.tier else {"safe"}

    plan = build_plan(root, tiers, args.semgrep_config)
    results: list[dict] = []
    generic_stats: dict = {"files_scanned": 0, "files_changed": 0, "skipped": {}}

    for entry in plan:
        name, tier = entry["name"], entry["tier"]
        if name == "generic":
            generic_stats = scan_generic(root, dry_run=args.dry_run)
            noun = "files would be normalized" if args.dry_run else "files normalized"
            detail = f"{generic_stats['files_changed']} of {generic_stats['files_scanned']} {noun}"
            status = "planned" if args.dry_run else "fixed"
        elif name == "semgrep":
            status, detail = tool_semgrep(root, args.semgrep_config, dry_run=args.dry_run)
        else:
            status, detail = entry["runner"](root, dry_run=args.dry_run)
        results.append({"name": name, "tier": tier, "status": status, "detail": detail})
        print(f"[fix-engine] {name} ({tier}): {status} - {detail}", file=sys.stderr)

    write_reports(
        ReportSpec(
            manifest_dir=Path(args.manifest_dir),
            root=root,
            tiers=sorted(tiers),
            semgrep_config=args.semgrep_config,
            results=results,
            generic_stats=generic_stats,
            dry_run=args.dry_run,
        )
    )
    done = "dry-run complete" if args.dry_run else "fix pass complete"
    print(f"[fix-engine] {done}: see {args.manifest_dir}/summary.md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
