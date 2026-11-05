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
import datetime
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# Version-pinned shfmt, fetched only when *.sh files exist and no local
# shfmt is on PATH (same pattern the deep audit uses for osv-scanner).
SHFMT_VERSION = "v3.12.0"
SHFMT_URL = (
    "https://github.com/mvdan/sh/releases/download/"
    f"{SHFMT_VERSION}/shfmt_{SHFMT_VERSION}_linux_amd64"
)

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "fix",  # the engine's own output directory
}

MAX_TEXT_BYTES = 2_000_000  # never rewrite files larger than 2 MB


# ---------------------------------------------------------------------------
# file discovery - git-aware with a non-git fallback for tests
# ---------------------------------------------------------------------------


def find_files(root: Path, predicate) -> list[str]:
    """Return repo-relative paths matching predicate, respecting ignores."""
    if (root / ".git").exists():
        out = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        names = [n for n in out.stdout.splitlines() if n]
    else:
        names = []
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            parts = path.relative_to(root).parts
            if any(part in EXCLUDED_DIRS for part in parts[:-1]):
                continue
            names.append(str(path.relative_to(root)))
    # defense in depth: even git-tracked vendored files are never touched
    names = [n for n in names if not any(part in EXCLUDED_DIRS for part in Path(n).parts[:-1])]
    return sorted(n for n in names if predicate(root / n))


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


def _run(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, check=False, timeout=900
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, exc.__class__.__name__
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, tail[-1][:160] if tail else f"exit {proc.returncode}"
    lines = (proc.stdout or "").strip().splitlines()
    return True, lines[-1][:160] if lines else "ok"


def tool_ruff_format(root: Path, dry_run: bool) -> tuple[str, str]:
    if dry_run:
        return "planned", "ruff format ."
    if not shutil.which("ruff"):
        return "skipped:tool-missing", "ruff not on PATH"
    ok, detail = _run(["ruff", "format", "."], root)
    return ("fixed" if ok else "failed"), detail


def tool_ruff_fix(root: Path, dry_run: bool) -> tuple[str, str]:
    if dry_run:
        return "planned", "ruff check --fix ."
    if not shutil.which("ruff"):
        return "skipped:tool-missing", "ruff not on PATH"
    ok, detail = _run(["ruff", "check", "--fix", "."], root)
    return ("fixed" if ok else "failed"), detail


def tool_rustfmt(root: Path, dry_run: bool) -> tuple[str, str]:
    if dry_run:
        return "planned", "cargo fmt --all"
    if not shutil.which("cargo"):
        return "skipped:tool-missing", "cargo not on PATH"
    ok, detail = _run(["cargo", "fmt", "--all"], root)
    return ("fixed" if ok else "failed"), detail


def tool_gofmt(root: Path, dry_run: bool) -> tuple[str, str]:
    if dry_run:
        return "planned", "gofmt -w (all *.go outside vendor)"
    if not shutil.which("gofmt"):
        return "skipped:tool-missing", "gofmt not on PATH"
    go_files = find_files(root, lambda p: p.suffix == ".go")
    if not go_files:
        return "clean", "no go files"
    ok, detail = _run(["gofmt", "-w", *go_files], root)
    return ("fixed" if ok else "failed"), f"{len(go_files)} files: {detail}"


def tool_go_mod_tidy(root: Path, dry_run: bool) -> tuple[str, str]:
    if dry_run:
        return "planned", "go mod tidy"
    if not shutil.which("go"):
        return "skipped:tool-missing", "go not on PATH"
    ok, detail = _run(["go", "mod", "tidy"], root)
    return ("fixed" if ok else "failed"), detail


def tool_biome(root: Path, dry_run: bool) -> tuple[str, str]:
    if dry_run:
        return "planned", "biome check --write (safe fixes only)"
    ok, detail = _run(
        [
            "npx",
            "--yes",
            "@biomejs/biome",
            "check",
            "--write",
            "--no-errors-on-unmatched",
            ".",
        ],
        root,
    )
    return ("fixed" if ok else "failed"), detail


def tool_shfmt(root: Path, dry_run: bool) -> tuple[str, str]:
    if dry_run:
        return "planned", f"shfmt -w ({SHFMT_VERSION})"
    sh_files = find_files(root, lambda p: p.suffix == ".sh")
    if not sh_files:
        return "clean", "no shell files"
    binary = shutil.which("shfmt")
    if not binary:
        tmpdir = Path("/tmp/fix-engine-shfmt")
        tmpdir.mkdir(parents=True, exist_ok=True)
        binary = str(tmpdir / "shfmt")
        try:
            urllib.request.urlretrieve(SHFMT_URL, binary)
            Path(binary).chmod(0o755)
        except OSError as exc:
            return "skipped:tool-missing", f"download failed: {exc.__class__.__name__}"
    ok, detail = _run([binary, "-w", *sh_files], root)
    return ("fixed" if ok else "failed"), f"{len(sh_files)} files: {detail}"


def tool_sqlfluff(root: Path, dry_run: bool) -> tuple[str, str]:
    if dry_run:
        return "planned", "sqlfluff fix (ansi dialect when unconfigured)"
    if not shutil.which("sqlfluff"):
        return "skipped:tool-missing", "sqlfluff not on PATH"
    cmd = ["sqlfluff", "fix", ".", "--disable-progress-bar"]
    if not (root / ".sqlfluff").is_file():
        cmd += ["--dialect", "ansi"]
    ok, detail = _run(cmd, root)
    return ("fixed" if ok else "failed"), detail


def tool_markdownlint(root: Path, dry_run: bool) -> tuple[str, str]:
    if dry_run:
        return "planned", "markdownlint-cli2 --fix"
    # ship the default config only when the repo does not carry its own
    if not (root / ".markdownlint-cli2.jsonc").is_file():
        default_cfg = root / "configs" / ".markdownlint-cli2.jsonc"
        if default_cfg.is_file():
            (root / ".markdownlint-cli2.jsonc").write_text(
                default_cfg.read_text(encoding="utf-8"), encoding="utf-8"
            )
    ok, detail = _run(["npx", "--yes", "markdownlint-cli2", "--fix", "**/*.md"], root)
    return ("fixed" if ok else "failed"), detail


def tool_semgrep(root: Path, semgrep_config: str, dry_run: bool) -> tuple[str, str]:
    if dry_run:
        return "planned", f"semgrep --autofix --config {semgrep_config}"
    if not shutil.which("semgrep"):
        return "skipped:tool-missing", "semgrep not on PATH"
    args: list[str] = []
    for cfg in semgrep_config.split():
        if cfg:
            args += ["--config", cfg]
    ok, detail = _run(
        ["semgrep", "scan", "--autofix", "--metrics=off", "--quiet", *args, "."], root
    )
    return ("fixed" if ok else "failed"), detail


# ---------------------------------------------------------------------------
# stack detection - the same triggers the CI stacks use
# ---------------------------------------------------------------------------


def has_python(root: Path) -> bool:
    return bool(find_files(root, lambda p: p.suffix == ".py"))


def has_rust(root: Path) -> bool:
    return (root / "Cargo.toml").is_file()


def has_go(root: Path) -> bool:
    return (root / "go.mod").is_file()


def has_node(root: Path) -> bool:
    return (root / "package.json").is_file()


def has_shell(root: Path) -> bool:
    return bool(find_files(root, lambda p: p.suffix == ".sh"))


def has_sql(root: Path) -> bool:
    return bool(find_files(root, lambda p: p.suffix == ".sql"))


# ---------------------------------------------------------------------------
# the plan - order matters: language fixers, docs, then the normalizer last
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


def write_reports(
    manifest_dir: Path,
    root: Path,
    tiers: list[str],
    semgrep_config: str,
    results: list[dict],
    generic_stats: dict,
    dry_run: bool,
) -> None:
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
        Path(args.manifest_dir),
        root,
        sorted(tiers),
        args.semgrep_config,
        results,
        generic_stats,
        args.dry_run,
    )
    done = "dry-run complete" if args.dry_run else "fix pass complete"
    print(f"[fix-engine] {done}: see {args.manifest_dir}/summary.md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
