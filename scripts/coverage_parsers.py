#!/usr/bin/env python3
"""Coverage report parsers: four dialects, one normalized shape.

Split out of coverage_gate.py so the gate stays gate logic; this module
only knows how to read the mainstream toolchains' outputs:

- ``coverage.json``  Python ``coverage json``: totals.percent_covered,
    per-file executed/missing line lists.
- ``coverage.xml``   Cobertura XML: jest/c8/vitest ``--coverage-reporter
    cobertura``, PHPUnit, gocover-cobertura, ``cargo llvm-cov
    --cobertura`` - one dialect.
- ``jacoco.xml``     JaCoCo's own dialect (Maven ``target/site/jacoco/``
    or Gradle ``build/reports/jacoco/``): dispatch on the ``<report>``
    root tag, line rows are missed/covered instruction counts.
- ``cover.out``      Go native coverprofile (``go test -coverprofile``).

Every parser produces the same :class:`CoverageReport`: a total percent
plus per-file covered/missed line sets, which is the only shape the
overall floor and the new-code intersection need. A report that does
not match its promised dialect raises :class:`CoverageGateError` - the
gate fails closed, it never guesses.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from slop_files import read_text_lax


class CoverageGateError(Exception):
    """A requested gate cannot be evaluated - maps to exit 2."""


@dataclass
class FileCoverage:
    path: str
    percent: float
    covered: set[int] = field(default_factory=set)
    missed: set[int] = field(default_factory=set)


@dataclass
class CoverageReport:
    source: str  # python-json | cobertura | jacoco | go-cover
    path: str  # report file path, for the report table
    total_percent: float
    files: dict[str, FileCoverage]

    def entry_for(self, changed_path: str) -> FileCoverage | None:
        """Exact match first, then Go-module-style suffix match."""
        entry = self.files.get(changed_path)
        if entry is not None:
            return entry
        suffix = "/" + changed_path
        for candidate in self.files.values():
            if candidate.path.endswith(suffix):
                return candidate
        return None


def _normalize(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def _parse_python_json(path: Path) -> CoverageReport:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageGateError(f"unparseable coverage report {path}: {exc}") from exc
    totals = data.get("totals") or {}
    if "percent_covered" not in totals:
        # coverage.py always writes totals.percent_covered; its absence
        # means the file is not the report we were promised
        raise CoverageGateError(f"unparseable coverage report {path}: no totals.percent_covered")
    files: dict[str, FileCoverage] = {}
    for name, body in (data.get("files") or {}).items():
        summary = body.get("summary") or {}
        files[_normalize(name)] = FileCoverage(
            path=_normalize(name),
            percent=float(summary.get("percent_covered", 0.0)),
            covered=set(body.get("executed_lines") or []),
            missed=set(body.get("missing_lines") or []),
        )
    total = float(totals.get("percent_covered", 0.0))
    return CoverageReport("python-json", str(path), total, files)


def _parse_cobertura(root: ET.Element, path: Path) -> CoverageReport:
    rate = root.get("line-rate")
    if rate is None:
        raise CoverageGateError(f"unparseable coverage report {path}: no line-rate")
    files: dict[str, FileCoverage] = {}
    for klass in root.iter("class"):
        name = _normalize(klass.get("filename") or "")
        if not name:
            continue
        covered: set[int] = set()
        missed: set[int] = set()
        for line in klass.iter("line"):
            number = int(line.get("number", 0))
            if int(line.get("hits", 0)) > 0:
                covered.add(number)
            else:
                missed.add(number)
        count = len(covered) + len(missed)
        percent = 100.0 * len(covered) / count if count else 0.0
        files[name] = FileCoverage(name, percent, covered, missed)
    return CoverageReport("cobertura", str(path), 100.0 * float(rate), files)


def _jacoco_total(root: ET.Element, path: Path) -> float:
    """Report-level percent from JaCoCo's counter roll-up.

    LINE is the semantics the gate promises (per-file data is line
    sets); INSTRUCTION is the fallback when a report carries no LINE
    counter. Only the counters that are direct children of ``<report>``
    count: nested package/class counters are subsets that appear earlier
    in document order and would masquerade as the total.
    """
    for ctype in ("LINE", "INSTRUCTION"):
        for counter in root.findall("counter"):
            if counter.get("type") != ctype:
                continue
            missed = int(counter.get("missed", 0))
            covered = int(counter.get("covered", 0))
            denom = missed + covered
            if denom > 0:
                return 100.0 * covered / denom
            break  # empty counter of this type: try the next type
    raise CoverageGateError(f"unparseable coverage report {path}: no LINE/INSTRUCTION counter")


def _jacoco_lines(sourcefile: ET.Element, path: Path) -> tuple[set[int], set[int]]:
    """One ``<sourcefile>``'s covered/missed line numbers.

    A line counts as covered iff ``ci`` (covered instructions) is
    positive; ``mi > 0`` with no covered instruction is a miss.
    """
    covered: set[int] = set()
    missed: set[int] = set()
    for line in sourcefile.iter("line"):
        try:
            number = int(line.get("nr", 0))
            ci = int(line.get("ci", 0))
            mi = int(line.get("mi", 0))
        except ValueError as exc:
            raise CoverageGateError(f"unparseable coverage report {path}: {exc}") from exc
        if ci > 0:
            covered.add(number)
        elif mi > 0:
            missed.add(number)
    return covered, missed


def _parse_jacoco(root: ET.Element, path: Path) -> CoverageReport:
    """JaCoCo's own XML dialect - not Cobertura.

    ``<package name="com/example">`` holds ``<sourcefile>`` children
    whose ``<line nr mi ci>`` rows carry missed/covered instruction
    counts. The class path is the dotted package name folded to a
    directory plus the file name, so the new-code gate can intersect it
    with the diff directly.
    """
    files: dict[str, FileCoverage] = {}
    for package in root.iter("package"):
        pkg_dir = (package.get("name") or "").replace(".", "/")
        for sourcefile in package.iter("sourcefile"):
            name = (sourcefile.get("name") or "").strip()
            if not name:
                continue
            rel = f"{pkg_dir}/{name}" if pkg_dir else name
            covered, missed = _jacoco_lines(sourcefile, path)
            seen = len(covered) + len(missed)
            percent = 100.0 * len(covered) / seen if seen else 0.0
            files[_normalize(rel)] = FileCoverage(_normalize(rel), percent, covered, missed)
    if not files:
        raise CoverageGateError(f"unparseable coverage report {path}: no sourcefile rows")
    return CoverageReport("jacoco", str(path), _jacoco_total(root, path), files)


def _parse_xml(path: Path) -> CoverageReport:
    """Dispatch on the root tag: Cobertura says ``coverage``, JaCoCo says
    ``report`` - two dialects that share nothing but angle brackets."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise CoverageGateError(f"unparseable coverage report {path}: {exc}") from exc
    if root.tag == "report":
        return _parse_jacoco(root, path)
    return _parse_cobertura(root, path)


def _cover_row(row: str, path: Path, files: dict[str, FileCoverage]) -> tuple[int, int]:
    """(statements, hits) for one coverprofile row; mutates ``files``."""
    location, _, rest = row.partition(" ")
    stmts_text, _, count_text = rest.partition(" ")
    file_part, _, span = location.rpartition(":")
    if not file_part or "." not in span:
        return 0, 0
    name = _normalize(file_part)
    start_text, _, end_text = span.partition(",")
    try:
        start_line = int(start_text.split(".")[0])
        end_line = int(end_text.split(".")[0])
        stmts = int(stmts_text)
        hits = int(count_text)
    except ValueError as exc:
        raise CoverageGateError(f"unparseable coverage report {path}: {exc}") from exc
    entry = files.setdefault(name, FileCoverage(name, 0.0))
    span_lines = set(range(start_line, end_line + 1))
    if hits > 0:
        entry.covered |= span_lines
    else:
        entry.missed |= span_lines
    return stmts, hits


def _parse_go_cover(path: Path) -> CoverageReport:
    files: dict[str, FileCoverage] = {}
    total_stmts = 0
    covered_stmts = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CoverageGateError(f"unparseable coverage report {path}: {exc}") from exc
    for row in lines:
        if not row or row.startswith("mode:"):
            continue
        stmts, hits = _cover_row(row, path, files)
        total_stmts += stmts
        if hits > 0:
            covered_stmts += stmts
    if total_stmts <= 0:
        raise CoverageGateError(f"unparseable coverage report {path}: no statement rows")
    for entry in files.values():
        seen = len(entry.covered) + len(entry.missed)
        entry.percent = 100.0 * len(entry.covered) / seen if seen else 0.0
        entry.covered -= entry.missed
    total = 100.0 * covered_stmts / total_stmts
    return CoverageReport("go-cover", str(path), total, files)


def parse_report(path: Path) -> CoverageReport:
    """Dispatch on the report's actual shape, not just its name."""
    if path.name == "cover.out":
        return _parse_go_cover(path)
    text = read_text_lax(path)
    if text.lstrip().startswith("{"):
        return _parse_python_json(path)
    if text.lstrip().startswith("<"):
        return _parse_xml(path)
    return _parse_go_cover(path)


STANDARD_REPORTS = ("coverage.json", "coverage.xml", "cover.out")


def discover_reports(root: Path, explicit: list[str]) -> list[CoverageReport]:
    """Explicit paths win; otherwise the standard names at the root."""
    if explicit:
        candidates = [Path(p) for p in explicit]
        missing = [str(p) for p in candidates if not p.exists()]
        if missing:
            raise CoverageGateError(f"coverage report(s) not found: {', '.join(missing)}")
    else:
        candidates = [root / name for name in STANDARD_REPORTS if (root / name).exists()]
    return [parse_report(path) for path in candidates]
