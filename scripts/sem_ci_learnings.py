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
- The writer is held to the loader's own contracts before disk is
touched. The loader's required-field rule runs first - a lesson with
an empty source, or an empty pattern (which compiles and would
otherwise slip through), is refused - and the rendered document is
parsed back through the loader's parsing path, so the store only ever
grows by bytes the loader accepts. Any refusal leaves the existing
file byte-unchanged.
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

_REQUIRED_FIELDS = ("kind", "pattern", "remediation", "source")


def _missing_fields(kind: object, pattern: object, remediation: object, source: object) -> str:
    """The loader's required-field rule, shared by the loader and the writer.

    The writer runs the same rule before touching the store, so a
    lesson the loader would refuse - an empty source, or an empty
    pattern, which compiles and would otherwise slip through - never
    reaches disk and never breaks the store it lands in.
    """
    return ", ".join(
        name
        for name, value in (
            ("kind", kind),
            ("pattern", pattern),
            ("remediation", remediation),
            ("source", source),
        )
        if not value
    )


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
    missing = _missing_fields(*(row.get(field) for field in _REQUIRED_FIELDS))
    if missing:
        raise ValueError(f"learnings {path}: entry missing {missing}")
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


def _parse_store(text: str, origin: Path) -> tuple[Learning, ...]:
    """The loader's parsing path, shared by reads and by writes.

    ``record_learning`` re-parses its rendered document through this
    same path before writing, so the writer cannot emit a store the
    loader would refuse - the contract that keeps one bad record from
    poisoning every later load and diagnosis.
    """
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("version") != LEARNINGS_SCHEMA_VERSION:
        raise ValueError(f"learnings {origin}: expected version {LEARNINGS_SCHEMA_VERSION}")
    rows = data.get("learnings")
    if not isinstance(rows, list):
        raise ValueError(f"learnings {origin}: 'learnings' must be a list")
    return tuple(_learning_from_row(row, origin) for row in rows)


def load_learnings(path: Path) -> tuple[Learning, ...]:
    """Load the store fail-closed: wrong schema or bad lesson refuses."""
    origin = Path(path)
    return _parse_store(origin.read_text(encoding="utf-8"), origin)


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

    The loader's required-field rule runs before anything is read or
    written - a lesson missing its source, or its pattern (which
    compiles empty and would otherwise slip through), is refused with
    the existing file untouched. The rendered document is then parsed
    back through the loader's own parsing path; only a store the
    loader accepts reaches disk, so the writer can never poison the
    memory it grows.

    Duplicate (kind, pattern) updates the remediation and source in
    place - the newer lesson wins, the pattern does not grow twice.
    Returns the byte-stable rendered document; identical content always
    renders to identical bytes.
    """
    missing = _missing_fields(kind, pattern, remediation, source)
    if missing:
        raise ValueError(f"lesson missing {missing}")
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
    # indent=2, not 1: this store is a committed file, so its bytes obey
    # the repo's .editorconfig (left padding a multiple of 2). An odd
    # indent renders lines the hosted hygiene gate red-flags - the
    # writer must satisfy the repo's own formatting contract, proved by
    # test_recorded_store_obey_the_editorconfig_contract.
    rendered = json.dumps(_store_document(list(kept.values())), indent=2)
    _parse_store(rendered, store_path)  # the writer must eat its own output
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
