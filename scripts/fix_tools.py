"""Fix-engine tool runners (ruff, rustfmt, gofmt, biome, shfmt, sqlfluff,
markdownlint, semgrep) and the per-stack detectors that select them."""

from __future__ import annotations

import shutil
import subprocess
import urllib.request
from pathlib import Path


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


SHFMT_VERSION = "v3.12.0"
SHFMT_URL = (
    "https://github.com/mvdan/sh/releases/download/"
    f"{SHFMT_VERSION}/shfmt_{SHFMT_VERSION}_linux_amd64"
)


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
