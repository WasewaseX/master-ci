"""Contract tests for the unified fix engine.

Covers the documented contract in scripts/fix_engine.py:
- the plan reacts to the same stack markers the CI stacks use,
- tier gating (safe vs lint) and semgrep opt-in via --semgrep-config,
- the generic normalizer (trailing whitespace, final newline, Markdown
  hard-break preservation, binary and vendored files untouched),
- dry-run never mutates anything,
- the manifest/summary/PR-body reports exist with the documented schema,
- the engine always exits 0, even when tools fail.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fix_engine.py"
spec = importlib.util.spec_from_file_location("fix_engine", MOD_PATH)
assert spec is not None and spec.loader is not None
fe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fe
spec.loader.exec_module(fe)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_root(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return root


def plan_names(root: Path, tiers: set[str], semgrep_config: str = "") -> list[str]:
    return [entry["name"] for entry in fe.build_plan(root, tiers, semgrep_config)]


# ---------------------------------------------------------------------------
# the plan: stack detection and tier gating
# ---------------------------------------------------------------------------


def test_plan_minimal_repo_is_markdown_and_generic_only(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"README.md": "# hi\n"})
    names = plan_names(root, {"safe"})
    assert names == ["markdownlint", "generic"]


def test_plan_python_repo_respects_tiers(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"app.py": "x = 1\n"})
    safe = plan_names(root, {"safe"})
    assert "ruff-format" in safe and "ruff-fix" not in safe
    both = plan_names(root, {"safe", "lint"})
    assert "ruff-format" in both and "ruff-fix" in both
    lint_only = plan_names(root, {"lint"})
    assert "ruff-format" not in lint_only and "ruff-fix" in lint_only


def test_plan_generic_is_always_last(tmp_path: Path) -> None:
    root = make_root(
        tmp_path,
        {
            "app.py": "x = 1\n",
            "go.mod": "module x\n",
            "main.go": "package main\n",
            "README.md": "# hi\n",
        },
    )
    names = plan_names(root, {"safe", "lint"})
    assert names[-1] == "generic"
    assert names.index("gofmt") < names.index("markdownlint")


@pytest.mark.parametrize(
    "marker,files,expected",
    [
        ("rust", {"Cargo.toml": "[package]\n"}, "rustfmt"),
        ("go", {"go.mod": "module x\n"}, "gofmt"),
        ("go-tidy-lint-only", {"go.mod": "module x\n"}, "go-mod-tidy"),
        ("node", {"package.json": "{}\n"}, "biome"),
        ("shell", {"run.sh": "#!/bin/sh\n"}, "shfmt"),
        ("sql", {"schema.sql": "SELECT 1;\n"}, "sqlfluff"),
    ],
)
def test_plan_reacts_to_stack_markers(
    tmp_path: Path, marker: str, files: dict, expected: str
) -> None:
    root = make_root(tmp_path, files)
    tiers = {"safe"} if marker.startswith(("rust", "node", "shell")) else {"safe", "lint"}
    assert expected in plan_names(root, tiers)


def test_plan_sqlfluff_is_lint_tier_only(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"schema.sql": "SELECT 1;\n"})
    assert "sqlfluff" not in plan_names(root, {"safe"})
    assert "sqlfluff" in plan_names(root, {"safe", "lint"})


def test_semgrep_requires_explicit_config(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"README.md": "# hi\n"})
    assert "semgrep" not in plan_names(root, {"safe", "lint"})
    assert "semgrep" in plan_names(root, {"safe", "lint"}, "p/default")


def test_excluded_dirs_never_produce_stack_markers(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"node_modules/pkg/index.js": "x\n", "README.md": "# hi\n"})
    assert "biome" not in plan_names(root, {"safe"})


# ---------------------------------------------------------------------------
# the generic normalizer
# ---------------------------------------------------------------------------


def test_generic_strips_trailing_whitespace_and_adds_newline(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"a.txt": "alpha   \nbeta\t\n"})
    fe.scan_generic(root, dry_run=False)
    assert (root / "a.txt").read_text() == "alpha\nbeta\n"


def test_generic_collapses_trailing_blank_lines(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"a.txt": "x\n\n\n"})
    fe.scan_generic(root, dry_run=False)
    assert (root / "a.txt").read_text() == "x\n"


def test_generic_preserves_markdown_hard_break(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"a.md": "one  \ntwo   \n"})
    fe.scan_generic(root, dry_run=False)
    # exactly-two-space break kept; three-space run stripped
    assert (root / "a.md").read_text() == "one  \ntwo\n"


def test_generic_never_touches_binary_or_vendored(tmp_path: Path) -> None:
    root = make_root(
        tmp_path,
        {
            "blob.bin": b"data\x00binary\n",
            "node_modules/dep.py": "keep me   \n",
            "vendor/lib.go": "package lib   \n",
            "latin.txt": "caf\xe9-latin1\n".encode("latin-1"),
        },
    )
    stats = fe.scan_generic(root, dry_run=False)
    assert (root / "blob.bin").read_bytes() == b"data\x00binary\n"
    assert (root / "node_modules/dep.py").read_text() == "keep me   \n"
    assert (root / "vendor/lib.go").read_text() == "package lib   \n"
    assert (root / "latin.txt").read_bytes() == "caf\xe9-latin1\n".encode("latin-1")
    assert stats["files_changed"] == 0


def test_generic_dry_run_counts_but_never_mutates(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"a.txt": "dirty   \n"})
    stats = fe.scan_generic(root, dry_run=True)
    assert stats["files_changed"] == 1
    assert (root / "a.txt").read_text() == "dirty   \n"


def test_normalize_file_reasons(tmp_path: Path) -> None:
    binary = tmp_path / "b.bin"
    binary.write_bytes(b"\x00\x01")
    assert fe.normalize_file(binary) == (False, "binary")
    clean = tmp_path / "c.txt"
    clean.write_text("ok\n")
    assert fe.normalize_file(clean) == (False, "already-clean")


def test_empty_content_stays_empty(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"empty.txt": ""})
    stats = fe.scan_generic(root, dry_run=False)
    assert (root / "empty.txt").read_text() == ""
    assert stats["files_changed"] == 0


# ---------------------------------------------------------------------------
# CLI: exit codes, dry-run purity, reports
# ---------------------------------------------------------------------------


def test_cli_dry_run_exits_zero_and_writes_reports(tmp_path: Path, capsys) -> None:
    root = make_root(tmp_path, {"app.py": "x = 1\n", "README.md": "# hi\n"})
    out = tmp_path / "fix"
    code = fe.main(
        [
            "--root",
            str(root),
            "--tier",
            "safe",
            "--tier",
            "lint",
            "--semgrep-config",
            "p/default",
            "--manifest-dir",
            str(out),
            "--dry-run",
        ]
    )
    assert code == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["mode"] == "dry-run plan"
    assert manifest["tiers"] == ["lint", "safe"]
    assert manifest["semgrep_config"] == "p/default"
    names = [t["name"] for t in manifest["tools"]]
    assert names == ["ruff-format", "markdownlint", "ruff-fix", "semgrep", "generic"]
    assert all(t["status"] == "planned" for t in manifest["tools"])
    assert "Safety rails" in (out / "pr-body.md").read_text()
    assert "| tool |" in (out / "summary.md").read_text()
    assert (root / "app.py").read_text() == "x = 1\n"
    dry_output = capsys.readouterr().err
    assert "[fix-engine]" in dry_output


def test_cli_default_tier_is_safe_only(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"app.py": "x = 1\n", "schema.sql": "SELECT 1;\n"})
    out = tmp_path / "fix"
    code = fe.main(["--root", str(root), "--manifest-dir", str(out), "--dry-run"])
    assert code == 0
    manifest = json.loads((out / "manifest.json").read_text())
    names = [t["name"] for t in manifest["tools"]]
    assert "ruff-fix" not in names and "sqlfluff" not in names
    assert manifest["tiers"] == ["safe"]


def test_cli_reports_are_valid_when_nothing_to_fix(tmp_path: Path) -> None:
    root = make_root(tmp_path, {"README.md": "# hi\n"})
    out = tmp_path / "fix"
    code = fe.main(["--root", str(root), "--manifest-dir", str(out)])
    assert code == 0
    manifest = json.loads((out / "manifest.json").read_text())
    gen = manifest["generic"]
    assert gen["files_changed"] == 0
    assert gen["files_scanned"] == 1


def test_fix_text_idempotent() -> None:
    cases = ["x\n", "one  \n", "three\n", ""]
    for text in cases:
        once = fe._fix_text(text, ".txt")
        assert fe._fix_text(once, ".txt") == once


# ---------------------------------------------------------------------------
# engine-vs-CI parity guard: the fix plan must stay a subset of what the
# deep audit and the CI stacks actually check, so fixes can never drift
# away from checks.
# ---------------------------------------------------------------------------


def test_engine_tools_are_the_ci_toolchain() -> None:
    """Every planned tool must be a documented member of the fix policy."""
    documented = {
        "ruff-format",
        "rustfmt",
        "gofmt",
        "biome",
        "shfmt",
        "markdownlint",
        "ruff-fix",
        "go-mod-tidy",
        "sqlfluff",
        "semgrep",
        "generic",
    }
    plan = fe.build_plan(Path(__file__).resolve().parents[1], {"safe", "lint"}, "p/default")
    assert {e["name"] for e in plan} <= documented
