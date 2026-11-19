"""The change stack: logical cohorts for a diff, ordered by dependency
direction (roadmap B23, reverse-engineered from CodeRabbit's Change
Stack).

A large diff is unreadable as a flat file list. The change stack groups
the changed files into cohorts by functional responsibility - data
model, API, service, UI, tests, build, config, docs - and orders the
cohorts foundation-first, so a reviewer reads the migration before the
endpoint that consumes it. Membership is path-rule based and
deterministic: the same input always produces the same stack. A file
that matches no rule lands in `unclassified` and is shown, never
hidden.

The dependency direction is a fixed layering, so a cycle cannot exist
by construction. That is the honest limitation versus a real dependency
graph, which the symbol graph (B25) supplies in the next slice; the
stack documents it instead of pretending to detect cycles.

Input: a git numstat stream (`path<TAB>added<TAB>deleted` per line) or
a plain path list. Output: markdown (the review comment) and JSON.

Exit codes: 0 scored, 2 usage error (empty input).
"""

from __future__ import annotations

import argparse
import dataclasses as dc
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Sequence

COHORT_ORDER = ("data-model", "api", "service", "ui", "tests", "build", "config", "docs")

#: First match wins; the rule order IS the precedence. Tests pin it.
COHORT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("data-model", ("migrations/", "models/", "schema", "entities/", "/db/", "orm")),
    ("api", ("routes/", "api/", "endpoints/", "openapi", "graphql", "controllers/")),
    ("service", ("services/", "core/", "domain/", "usecases/", "engine")),
    ("ui", ("components/", "pages/", "views/", "styles/", "ui/", ".css", ".scss")),
    ("tests", ("tests/", "test_", "_test.", "spec/", "conftest")),
    ("build", ("makefile", "dockerfile", ".github/workflows/", "scripts/", "gradle", "cargo.toml", "package.json")),
    ("config", ("config", "settings", ".toml", ".yaml", ".yml", ".json", "manifest", ".ini")),
    ("docs", (".md", "docs/", "readme", "changelog")),
)


@dc.dataclass
class ChangeFile:
    """One changed file with its measured size when numstat gave one."""

    path: str
    added: int | None = None
    deleted: int | None = None


@dc.dataclass
class Cohort:
    """One functional cohort, foundation-first in the stack order."""

    name: str
    files: list[ChangeFile] = dc.field(default_factory=list)

    @property
    def added(self) -> int | None:
        counts = [f.added for f in self.files]
        return sum(c for c in counts if c is not None) if any(c is not None for c in counts) else None

    @property
    def deleted(self) -> int | None:
        counts = [f.deleted for f in self.files]
        return sum(c for c in counts if c is not None) if any(c is not None for c in counts) else None


def classify(path: str) -> str:
    """The cohort name for one path; first matching rule wins."""
    norm = path.replace("\\", "/").lower()
    for cohort, needles in COHORT_RULES:
        for needle in needles:
            if needle in norm:
                return cohort
    return "unclassified"


def parse_numstat(lines: list[str]) -> list[ChangeFile]:
    """`path<TAB>added<TAB>deleted` rows, or bare paths; '-' rows become None."""
    files: list[ChangeFile] = []
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) == 3 and all(p.strip().isdigit() or p.strip() == "-" for p in parts[:2]):
            added = None if parts[0].strip() == "-" else int(parts[0])
            deleted = None if parts[1].strip() == "-" else int(parts[1])
            files.append(ChangeFile(path=parts[2].strip(), added=added, deleted=deleted))
        else:
            files.append(ChangeFile(path=line.strip()))
    return files


def build_stack(files: list[ChangeFile]) -> list[Cohort]:
    """Cohorts in dependency order, files sorted inside each cohort."""
    buckets: dict[str, Cohort] = {name: Cohort(name=name) for name in COHORT_ORDER}
    buckets["unclassified"] = Cohort(name="unclassified")
    for change in sorted(files, key=lambda f: f.path):
        buckets[classify(change.path)].files.append(change)
    order = list(COHORT_ORDER) + ["unclassified"]
    return [buckets[name] for name in order if buckets[name].files]


def render_markdown(stack: list[Cohort]) -> str:
    """The review-comment form: cohort sections with sizes and files."""
    lines = ["# Change stack", ""]
    for cohort in stack:
        added = cohort.added
        deleted = cohort.deleted
        size = "size n/a" if added is None else f"+{added}/-{deleted}"
        lines.append(f"## {cohort.name} ({len(cohort.files)} files, {size})")
        for change in cohort.files:
            if change.added is None:
                lines.append(f"- {PurePosixPath(change.path).as_posix()}")
            else:
                lines.append(
                    f"- {PurePosixPath(change.path).as_posix()} (+{change.added}/-{change.deleted})"
                )
        lines.append("")
    lines.append("Cohorts are ordered foundation-first; `unclassified` files are shown, not hidden.")
    lines.append("")
    return "\n".join(lines)


def stack_to_json(stack: list[Cohort]) -> dict:
    """The machine-readable form with the cohort order declared."""
    return {
        "cohort_order": list(COHORT_ORDER) + ["unclassified"],
        "cohorts": [
            {
                "name": c.name,
                "files": [vars(f) for f in c.files],
                "added": c.added,
                "deleted": c.deleted,
            }
            for c in stack
        ],
    }


def main(argv: Sequence[str] | None = None, /) -> int:
    """CLI: read the diff from stdin or --files, emit markdown or JSON."""
    parser = argparse.ArgumentParser(description="group a diff into dependency-ordered cohorts")
    parser.add_argument("--files", help="path to a numstat or path-list file (default: stdin)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    if args.files:
        source = Path(args.files)
        if not source.is_file():
            print(f"change-stack: input file not found: {source}", file=sys.stderr)
            return 2
        text = source.read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    files = parse_numstat(text.splitlines())
    if not files:
        print("change-stack: no input files", file=sys.stderr)
        return 2
    stack = build_stack(files)
    if args.json:
        print(json.dumps(stack_to_json(stack), indent=2))
    else:
        print(render_markdown(stack), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
