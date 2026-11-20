"""Persistent reviewer learnings (roadmap B28 slice, round-18).

Greptile's loop is review -> findings -> memory: what a reviewer
taught you today must still be known tomorrow. Until now the CI
diagnoser forgot everything between runs - a failure its signature
table did not know stayed unknown forever, no matter how many times it
repeated. The learnings store is the memory half of that loop: one
versioned JSON document of lessons, each a failure signature regex,
the remediation that actually fixed it and the review that taught it.

The contracts, all test-pinned:

- Fail-closed loading. A store with a wrong version, a missing field,
an empty remediation or an uncompilable pattern raises before any
diagnosis runs - a memory that cannot be trusted must refuse loudly,
never silently classify with half a lesson.
- Deterministic recording. Lessons are deduplicated on (kind, pattern),
a re-recorded lesson updates its remediation and source in place, the
list is sorted by kind then pattern, and identical inputs produce
byte-identical stores, so a re-record cannot churn a reviewed file.
- ``learned`` confidence. ``diagnose_log`` tries the signature table
first, the learnings store second, and reports a learned match as
``learned`` with the lesson's remediation - yesterday's unknown
failure becomes today's classified one, with the receipt of where
the knowledge came from.

The store ships empty and grows only from real evidence: a lesson is
recorded because a review or a run taught it, never invented to fill
the table. This is the B28 slice - store, load, record and diagnosis
integration; the full review-state lifecycle stays open in batch 4.
"""

from __future__ import annotations

import argparse
import dataclasses as dc
import json
import re
import sys
from pathlib import Path
from typing import Sequence

LEARNINGS_SCHEMA_VERSION = 1


@dc.dataclass(frozen=True)
class Learning:
    """One review lesson: signature, fix, provenance, compiled once."""

    kind: str
    pattern: str
    remediation: str
    source: str
    compiled: re.Pattern[str] = dc.field(compare=False)

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.remediation.strip():
            raise ValueError("learning needs a non-empty kind and remediation")


def _learning_from_row(row: object, path: Path) -> Learning:
    """Validate one store row and compile its pattern (fail-closed)."""
    if not isinstance(row, dict):
        raise ValueError(f"learnings {path}: each entry must be an object")
    missing = [
        field for field in ("kind", "pattern", "remediation", "source") if not row.get(field)
    ]
    if missing:
        raise ValueError(f"learnings {path}: entry missing {', '.join(missing)}")
    raw_pattern = str(row["pattern"])
    try:
        compiled = re.compile(raw_pattern)
    except re.error as exc:
        raise ValueError(
            f"learnings {path}: pattern {raw_pattern!r} does not compile: {exc}"
        ) from exc
    return Learning(
        kind=str(row["kind"]),
        pattern=raw_pattern,
        remediation=str(row["remediation"]),
        source=str(row["source"]),
        compiled=compiled,
    )


def load_learnings(path: Path) -> tuple[Learning, ...]:
    """Load the store fail-closed: wrong schema or bad lesson refuses."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != LEARNINGS_SCHEMA_VERSION:
        raise ValueError(f"learnings {path}: expected version {LEARNINGS_SCHEMA_VERSION}")
    rows = data.get("learnings")
    if not isinstance(rows, list):
        raise ValueError(f"learnings {path}: 'learnings' must be a list")
    return tuple(_learning_from_row(row, Path(path)) for row in rows)


def _store_document(learnings: Sequence[Learning]) -> dict:
    """The versioned document, lessons sorted kind-then-pattern."""
    ordered = sorted(learnings, key=lambda lesson: (lesson.kind, lesson.pattern))
    return {
        "version": LEARNINGS_SCHEMA_VERSION,
        "learnings": [
            {
                "kind": lesson.kind,
                "pattern": lesson.pattern,
                "remediation": lesson.remediation,
                "source": lesson.source,
            }
            for lesson in ordered
        ],
    }


def record_learning(path: Path, kind: str, pattern: str, remediation: str, source: str) -> str:
    """One lesson in, the whole store rewritten deterministically.

    Duplicate (kind, pattern) updates the remediation and source in
    place - the newer lesson wins, the pattern does not grow twice.
    Returns the byte-stable rendered document; identical content always
    renders to identical bytes.
    """
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"learning pattern {pattern!r} does not compile: {exc}") from exc
    learning = Learning(
        kind=kind, pattern=pattern, remediation=remediation, source=source, compiled=compiled
    )
    store_path = Path(path)
    existing: list[Learning]
    if store_path.is_file():
        existing = list(load_learnings(store_path))
    else:
        existing = []
    kept: dict[tuple[str, str], Learning] = {
        (lesson.kind, lesson.pattern): lesson for lesson in existing
    }
    kept[(learning.kind, learning.pattern)] = learning
    rendered = json.dumps(_store_document(list(kept.values())), indent=1)
    store_path.write_text(rendered + "\n", encoding="utf-8")
    return rendered


def main(argv: Sequence[str] | None = None, /) -> int:
    """CLI: --store PATH with --show or --record (+ --kind/--pattern/...).

    Exit 0 shown or recorded, 2 refused (the configuration-error
    class): a malformed store, an uncompilable pattern or a lesson
    missing its fix never reaches the file.
    """
    parser = argparse.ArgumentParser(
        description="show or record persistent reviewer learnings for CI diagnosis"
    )
    parser.add_argument("--store", required=True, help="the learnings JSON store")
    parser.add_argument("--show", action="store_true", help="print the store's lessons")
    parser.add_argument("--record", action="store_true", help="record one lesson into the store")
    parser.add_argument("--kind", default="", help="short label for the failure class")
    parser.add_argument("--pattern", default="", help="failure signature regex")
    parser.add_argument("--remediation", default="", help="the fix that worked")
    parser.add_argument("--source", default="", help="the review or run that taught this")
    args = parser.parse_args(argv)
    if not args.show and not args.record:
        parser.error("choose --show or --record")
    try:
        if args.show:
            for lesson in load_learnings(Path(args.store)):
                print(
                    f"{lesson.kind}: /{lesson.pattern}/ -> {lesson.remediation} ({lesson.source})"
                )
        else:
            record_learning(
                Path(args.store), args.kind, args.pattern, args.remediation, args.source
            )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ci-learnings refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
