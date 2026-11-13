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

import importlib.util
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "master-ci.yml"

# The translator is shared production code now: the MCP server's
# filter_paths tool and this regression suite MUST use one implementation
# so they can never drift apart. The import below rebinds the historical
# names; every assertion in this file stays exactly as it was.
_PF_PATH = ROOT / "scripts" / "path_filters.py"
_spec = importlib.util.spec_from_file_location("path_filters", _PF_PATH)
assert _spec is not None and _spec.loader is not None
_pf = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _pf
_spec.loader.exec_module(_pf)

UnsupportedPatternError = _pf.UnsupportedPatternError
_translate = _pf._translate
FilterSet = _pf.FilterSet
load_filters = _pf.load_filters


def _on(data: dict) -> dict:
    """The `on:` mapping - PyYAML 1.1 parses the key as boolean True."""
    return data[True] if True in data else data["on"]


FILTERS = _pf.FILTERS
stacks_for = _pf.stacks_for


def test_fallback_table_matches_live_workflow() -> None:
    """The committed snapshot must equal the live table, name for name."""
    live = {name: sorted(fs.positive) + sorted(fs.negative) for name, fs in FILTERS.items()}
    live = {name: sorted(set(patterns)) for name, patterns in live.items()}
    fallback_sets = _pf.load_fallback_filters()
    fallback = {
        name: sorted(fs.positive) + sorted(fs.negative) for name, fs in fallback_sets.items()
    }
    fallback = {name: sorted(set(patterns)) for name, patterns in fallback.items()}
    assert live == fallback, "path_filters_fallback.json has drifted from master-ci.yml"


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
    # Other languages: now full CI stacks too (v1.9.0)
    "vendor/x/z.go": frozenset(),
    "lib/User.java": frozenset({"code", "java"}),
    "app/models/user.rb": frozenset({"code", "ruby"}),
    "public/index.php": frozenset({"code", "php"}),
    "src/vector.c": frozenset({"code", "cpp"}),
    "src/vector.cpp": frozenset({"code", "cpp"}),
    "include/vector.h": frozenset({"code", "cpp"}),
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
    "Dockerfile.dev": frozenset({"docker"}),
    "compose.yaml": frozenset({"docker"}),
    "docker-compose.override.yml": frozenset({"docker"}),
    ".dockerignore": frozenset({"docker"}),
    # JVM (Java, Kotlin, Scala via Maven/Gradle)
    "pom.xml": frozenset({"java"}),
    "module/pom.xml": frozenset({"java"}),
    "build.gradle": frozenset({"java"}),
    "build.gradle.kts": frozenset({"code", "java"}),
    "settings.gradle.kts": frozenset({"code", "java"}),
    "gradle/wrapper/gradle-wrapper.properties": frozenset({"java"}),
    "src/main/java/app/User.java": frozenset({"code", "java"}),
    "src/main/kotlin/app/Main.kt": frozenset({"code", "java"}),
    ".mvn/maven.config": frozenset({"java"}),
    # C/C++
    "src/kernel.cc": frozenset({"code", "cpp"}),
    "include/util.hpp": frozenset({"code", "cpp"}),
    "CMakeLists.txt": frozenset({"cpp"}),
    "cmake/toolchain.cmake": frozenset({"cpp"}),
    ".clang-format": frozenset({"cpp"}),
    # .NET (C#, F#)
    "src/App/Program.cs": frozenset({"code", "csharp"}),
    "src/App/App.csproj": frozenset({"csharp"}),
    "src/Lib/Lib.fsproj": frozenset({"csharp"}),
    "App.sln": frozenset({"csharp"}),
    "global.json": frozenset({"csharp"}),
    "Directory.Build.props": frozenset({"csharp"}),
    # Ruby
    "Gemfile": frozenset({"ruby"}),
    "Gemfile.lock": frozenset({"ruby"}),
    "Rakefile": frozenset({"ruby"}),
    "mygem.gemspec": frozenset({"ruby"}),
    "lib/mygem/version.rb": frozenset({"code", "ruby"}),
    "app/views/index.html.erb": frozenset({"code", "ruby"}),
    ".rubocop.yml": frozenset({"ruby"}),
    # PHP
    "composer.json": frozenset({"php"}),
    "composer.lock": frozenset({"php"}),
    "src/Kernel.php": frozenset({"code", "php"}),
    "phpunit.xml.dist": frozenset({"php"}),
    "phpstan.neon.dist": frozenset({"php"}),
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
    "vendor/bundle/gems/lib.rb": frozenset(),
    "vendor/php/package/Kernel.php": frozenset(),
    "bin/Debug/net10.0/App.dll": frozenset(),
    "obj/Debug/x.cs": frozenset(),
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
    trigger = _on(data)
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
