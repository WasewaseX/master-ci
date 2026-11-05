"""Path-filter regression tests for the `changes` job of master-ci.yml.

dorny/paths-filter (pinned, `predicate-quantifier: some-with-excludes`)
matches each changed file against the filter table using picomatch
semantics. These tests extract the table straight from the workflow,
re-implement the matching semantics (globstar, single-star, question mark,
brace groups, negation exclusions, dotfiles matched), and assert which
stacks fire for synthetic repository layouts - one file or one set of
files each. A filter edit that silently breaks stack detection (the
failure mode flagged in the Phase 1 review) fails here before it fails a
consumer's CI.

Semantics implemented per dorny + picomatch:
- `**/` can match zero directories (`**/package.json` matches the root one)
- `a/**` matches everything below `a`, not `a` itself
- `*` and `?` never match `/`
- negated patterns are exclusions under some-with-excludes: a filter
    matches only when at least one positive pattern matches and no
    negated pattern does
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "master-ci.yml"


# ---------------------------------------------------------------------------
# picomatch-subset glob translation
# ---------------------------------------------------------------------------


class UnsupportedPatternError(ValueError):
    """Raised when a pattern uses syntax the translator does not support."""


_ALLOWED = re.compile(r"^[\w .\-/@{,}!?$*+~^&=%]*$")


def _translate_brace_group(glob: str, i: int) -> tuple[str, int]:
    """Translate a ``{a,b}`` group starting at ``i``; return (regex, next_i)."""
    end = glob.index("}", i)
    body = glob[i + 1 : end]
    branches = [_translate(branch) for branch in body.split(",")]
    return "(?:" + "|".join(branches) + ")", end + 1


def _next_token(glob: str, i: int) -> tuple[str, int]:
    """Translate the glob token starting at ``i``; return (regex, advance)."""
    chunk = glob[i:]
    if chunk.startswith("**/"):
        return r"(?:[^/]+/)*", 3
    if chunk.startswith("**"):
        return r".*", 2
    if chunk.startswith("*"):
        return r"[^/]*", 1
    if chunk.startswith("?"):
        return r"[^/]", 1
    if chunk.startswith("{"):
        return _translate_brace_group(glob, i)
    return re.escape(glob[i]), 1


def _translate(glob: str) -> str:
    if not _ALLOWED.match(glob):
        raise UnsupportedPatternError(f"unsupported pattern syntax: {glob!r}")
    out: list[str] = []
    i = 0
    while i < len(glob):
        piece, advance = _next_token(glob, i)
        out.append(piece)
        i += advance
    return "".join(out)


class FilterSet:
    """One named dorny filter group: positives minus negations."""

    def __init__(self, name: str, patterns: list[str]) -> None:
        self.name = name
        self.positive = [p for p in patterns if not p.startswith("!")]
        self.negative = [p[1:] for p in patterns if p.startswith("!")]
        self._positive_re = [re.compile(_translate(p)) for p in self.positive]
        self._negative_re = [re.compile(_translate(p)) for p in self.negative]

    def matches(self, path: str) -> bool:
        if any(rx.fullmatch(path) for rx in self._negative_re):
            return False
        return any(rx.fullmatch(path) for rx in self._positive_re)


def load_filters(workflow: Path = WORKFLOW) -> dict[str, FilterSet]:
    data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    changes = data["jobs"]["changes"]
    step = next(
        s for s in changes["steps"] if str(s.get("uses", "")).startswith("dorny/paths-filter")
    )
    table = yaml.safe_load(step["with"]["filters"])
    return {name: FilterSet(name, patterns or []) for name, patterns in table.items()}


FILTERS = load_filters()


def stacks_for(path: str) -> frozenset[str]:
    return frozenset(name for name, fs in FILTERS.items() if fs.matches(path))


# ---------------------------------------------------------------------------
# Single-file synthetic layouts: changed path -> exactly these stacks fire
# ---------------------------------------------------------------------------

SINGLE_FILE_CASES: dict[str, frozenset[str]] = {
    # Python
    "main.py": frozenset({"code", "python"}),
    "src/pkg/mod.py": frozenset({"code", "python"}),
    "deeply/nested/dir/module.py": frozenset({"code", "python"}),
    "pyproject.toml": frozenset({"python"}),
    "packages/app/pyproject.toml": frozenset({"python"}),
    "requirements.txt": frozenset({"python"}),
    "requirements-dev.txt": frozenset({"python"}),
    "src/requirements.prod.txt": frozenset({"python"}),
    "setup.py": frozenset({"code", "python"}),
    "setup.cfg": frozenset({"python"}),
    "mypy.ini": frozenset({"python"}),
    "ruff.toml": frozenset({"python"}),
    ".ruff.toml": frozenset({"python"}),
    # Rust
    "src/main.rs": frozenset({"code", "rust"}),
    "src/lib.rs": frozenset({"code", "rust"}),
    "crates/core/src/lib.rs": frozenset({"code", "rust"}),
    "Cargo.toml": frozenset({"rust"}),
    "crates/core/Cargo.toml": frozenset({"rust"}),
    "Cargo.lock": frozenset({"rust"}),
    "rust-toolchain.toml": frozenset({"rust"}),
    "rust-toolchain": frozenset({"rust"}),
    "deny.toml": frozenset({"rust"}),
    # Node / TypeScript
    "package.json": frozenset({"node"}),
    "packages/app/package.json": frozenset({"node"}),
    "package-lock.json": frozenset({"node"}),
    "pnpm-lock.yaml": frozenset({"node"}),
    "yarn.lock": frozenset({"node"}),
    "index.ts": frozenset({"code", "node"}),
    "src/index.ts": frozenset({"code", "node"}),
    "src/app.ts": frozenset({"code", "node"}),
    "src/app.tsx": frozenset({"code", "node"}),
    "lib/util.mjs": frozenset({"code", "node"}),
    "scripts/build.cjs": frozenset({"code", "node"}),
    "app.jsx": frozenset({"code", "node"}),
    "biome.json": frozenset({"node"}),
    "biome.jsonc": frozenset({"node"}),
    "tsconfig.json": frozenset({"node"}),
    "packages/app/tsconfig.base.json": frozenset({"node"}),
    # Go
    "main.go": frozenset({"code", "go"}),
    "cmd/server/main.go": frozenset({"code", "go"}),
    "cmd/x/z.go": frozenset({"code", "go"}),
    "internal/store/db.go": frozenset({"code", "go"}),
    "go.mod": frozenset({"go"}),
    "go.sum": frozenset({"go"}),
    "go.work": frozenset({"go"}),
    ".golangci.yml": frozenset({"go"}),
    # Other languages: code-level checks only
    "vendor/x/z.go": frozenset(),
    "lib/User.java": frozenset({"code"}),
    "app/models/user.rb": frozenset({"code"}),
    "public/index.php": frozenset({"code"}),
    "src/vector.c": frozenset({"code"}),
    "src/vector.cpp": frozenset({"code"}),
    "include/vector.h": frozenset({"code"}),
    "scripts/bootstrap.sh": frozenset({"code"}),
    # Docs
    "README.md": frozenset({"docs"}),
    "docs/guide.md": frozenset({"docs"}),
    "docs/guide.mdx": frozenset({"docs"}),
    "docs/manual.rst": frozenset({"docs"}),
    "deep/dir/note.md": frozenset({"docs"}),
    # CI config
    ".github/workflows/ci.yml": frozenset({"workflows"}),
    ".github/workflows/label.yml": frozenset({"workflows"}),
    ".github/workflows/master-ci.yml": frozenset({"workflows"}),
    ".github/workflows/deep/nested.yml": frozenset({"workflows"}),
    ".github/labeler.yml": frozenset({"workflows"}),
    ".github/dependabot.yml": frozenset({"workflows"}),
    ".github/codeql/codeql-config.yml": frozenset({"workflows"}),
    # Docker
    "Dockerfile": frozenset({"docker"}),
    "deploy/api/Dockerfile.prod": frozenset({"docker"}),
    "docker/app.dockerfile": frozenset({"docker"}),
    # Excluded: generated and vendored trees
    "dist/bundle.js": frozenset(),
    "packages/app/dist/index.js": frozenset(),
    "build/output.py": frozenset(),
    "target/debug/deps/libcore.rlib": frozenset(),
    "crates/core/target/release/build.rs": frozenset(),
    "vendor/github.com/x/y/z.go": frozenset(),
    "coverage/lcov-report/index.js": frozenset(),
    "node_modules/pkg/index.js": frozenset(),
    "node_modules/pkg/package.json": frozenset(),
    # Fixtures: excluded from the Node stack only (code checks still fire)
    "fixtures/demo/package.json": frozenset(),
    "fixtures/a/package.json": frozenset(),
    "fixtures/demo/pnpm-lock.yaml": frozenset(),
    "fixtures/demo/index.ts": frozenset({"code"}),
    "tests/fixtures/slop-code/messy.js": frozenset({"code"}),
    # Near misses that must not match
    "main.py.bak": frozenset(),
    "main.py.txt": frozenset(),
    "notapackage.json": frozenset(),
    "packages.json": frozenset(),
    "my.Dockerfile": frozenset(),
    "AGithub/workflows/ci.yml": frozenset(),
    "src/Cargo.toml.bak": frozenset(),
    "Cargo.toml.orig": frozenset(),
    "README.mdgen": frozenset(),
    "notes.txt": frozenset(),
    ".github/CODEOWNERS": frozenset(),
    ".github/workflows/ci.yml.bak": frozenset({"workflows"}),
    "Makefile": frozenset(),
    "config.yaml": frozenset(),
    ".editorconfig": frozenset(),
    ".gitignore": frozenset(),
    "LICENSE": frozenset(),
}


def test_single_file_layouts() -> None:
    mismatches = {
        path: (sorted(expected), sorted(stacks_for(path)))
        for path, expected in SINGLE_FILE_CASES.items()
        if stacks_for(path) != expected
    }
    assert not mismatches, f"{len(mismatches)} layout(s) mismatch: {mismatches}"


def test_case_count_is_meaningful() -> None:
    assert len(SINGLE_FILE_CASES) >= 70


# ---------------------------------------------------------------------------
# Cross-consistency: a filter group behaves as OR over changed files
# ---------------------------------------------------------------------------


def test_multi_file_layouts_are_the_union_of_single_files() -> None:
    combos = [
        ["README.md", "src/lib.rs"],
        ["package.json", "pnpm-lock.yaml"],
        ["main.py", "requirements.txt"],
        [".github/workflows/ci.yml", "src/lib.rs"],
        ["fixtures/a/package.json", "package.json"],
        ["Dockerfile", "main.go"],
        ["dist/bundle.js", "src/index.ts"],
        ["Cargo.toml", "Cargo.lock", "src/main.rs"],
        ["go.mod", "go.sum", "cmd/x/z.go"],
        ["setup.py", "crates/core/src/lib.rs"],
        ["docs/guide.md", "coverage/lcov-report/index.js"],
        ["node_modules/pkg/index.js", "src/index.ts"],
        ["vendor/x/z.go", "cmd/x/z.go"],
        ["pyproject.toml", "Cargo.toml", "package.json"],
        [".github/dependabot.yml", ".github/workflows/label.yml"],
        ["fixtures/demo/index.ts", "fixtures/demo/package.json", "src/app.ts"],
    ]
    for combo in combos:
        missing = [p for p in combo if p not in SINGLE_FILE_CASES]
        assert not missing, f"combo paths missing single-file coverage: {missing}"
        expected = frozenset().union(*(SINGLE_FILE_CASES[p] for p in combo))
        got = frozenset().union(*(stacks_for(p) for p in combo))
        assert got == expected, f"{combo}: expected {sorted(expected)}, got {sorted(got)}"


# ---------------------------------------------------------------------------
# Invariants on the filter table and its wiring
# ---------------------------------------------------------------------------


def test_negation_exclusions_need_some_with_excludes() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    changes = data["jobs"]["changes"]
    step = next(
        s for s in changes["steps"] if str(s.get("uses", "")).startswith("dorny/paths-filter")
    )
    has_negations = any(fs.negative for fs in FILTERS.values())
    quantifier = step["with"].get("predicate-quantifier", "")
    assert has_negations, "expected at least one negated exclusion pattern"
    assert quantifier == "some-with-excludes", (
        "negated patterns only exclude under some-with-excludes"
    )


def test_every_group_has_at_least_one_positive_pattern() -> None:
    for fs in FILTERS.values():
        assert fs.positive, f"filter {fs.name} has only negations and can never match"


def test_changes_outputs_cover_every_filter() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    outputs = data["jobs"]["changes"]["outputs"]
    declared = {name for name, spec in outputs.items() if "filter.outputs" in str(spec)}
    assert declared == set(FILTERS), f"output wiring mismatch: {sorted(declared)}"


def test_job_conditions_reference_declared_outputs() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    used = set(re.findall(r"needs\.changes\.outputs\.(\w+)", str(data)))
    assert used <= set(FILTERS), (
        f"jobs reference unknown filter outputs: {sorted(used - set(FILTERS))}"
    )


def test_every_stack_filter_has_a_force_run_input() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    trigger = data[True] if True in data else data["on"]
    inputs = trigger["workflow_call"]["inputs"]
    for stack in ("node", "python", "rust", "go"):
        assert stack in FILTERS, f"missing {stack} filter"
        assert f"run_{stack}" in inputs, f"missing run_{stack} input to force {stack}"


def test_patterns_stay_within_supported_syntax() -> None:
    for fs in FILTERS.values():
        for pattern in fs.positive + fs.negative:
            try:
                _translate(pattern)
            except UnsupportedPatternError as exc:  # pragma: no cover
                raise AssertionError(f"filter {fs.name}: {exc}") from exc


def test_translator_rejects_unsupported_constructs() -> None:
    for bad in ("[a-z]", "[!a-z]", "*(foo)", "+(bar)", "@(baz)"):
        try:
            _translate(bad)
        except UnsupportedPatternError:
            continue
        raise AssertionError(f"translator accepted unsupported pattern {bad!r}")


def test_translator_matches_picomatch_semantics() -> None:
    import re as _re

    def match(glob: str, path: str) -> bool:
        return bool(_re.compile(_translate(glob)).fullmatch(path))

    assert match("**/package.json", "package.json")
    assert match("**/package.json", "a/b/package.json")
    assert not match("**/package.json", "package.json2")
    assert match("a/**", "a/b/c.txt")
    assert not match("a/**", "a")
    assert match("a/**/b", "a/b")
    assert match("a/**/b", "a/x/y/b")
    assert not match("*", "a/b")
    assert match("*", ".hidden")
    assert match("**/*.py", "x.py")
    assert match("**/*.py", "a/b/x.py")
    assert not match("**/*.py", "a/x.py.bak")
    assert match("tsconfig*.json", "tsconfig.base.json")
    assert not match("tsconfig*.json", "tsconfig.json5")
