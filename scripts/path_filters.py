#!/usr/bin/env python3
"""path_filters.py - the master-ci change-classification engine, as a library.

This module owns the picomatch-subset glob translation used to decide which
CI stacks a change set activates. It powers two surfaces with one
implementation so they can never drift apart: the path-filter regression
suite (tests/test_path_filters.py) and the MCP server's ``filter_paths``
tool, which lets any AI assistant ask "which checks would run for these
files?" before writing them.

Semantics follow dorny/paths-filter with picomatch matching:
``**/`` can match zero directories, ``*`` and ``?`` never match ``/``,
brace groups expand, and negated patterns are exclusions: a filter fires
only when at least one positive pattern matches and no negated pattern
does.

The filter table itself lives in .github/workflows/master-ci.yml and is
the source of truth. ``filters_for_repo`` reads it when present and falls
back to scripts/path_filters_fallback.json (a committed snapshot of the
same table) otherwise; the parity test keeps the snapshot identical to the
live workflow, so the fallback is safe everywhere.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FALLBACK_PATH = Path(__file__).resolve().parent / "path_filters_fallback.json"


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


def load_filters(workflow: Path) -> dict[str, FilterSet]:
    """Parse the filter table straight out of a master-ci workflow file."""
    import yaml  # type: ignore[import-untyped]  # local import: only needed when a live workflow is parsed

    data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    changes = data["jobs"]["changes"]
    step = next(
        s for s in changes["steps"] if str(s.get("uses", "")).startswith("dorny/paths-filter")
    )
    table = yaml.safe_load(step["with"]["filters"])
    return {name: FilterSet(name, patterns or []) for name, patterns in table.items()}


def load_fallback_filters() -> dict[str, FilterSet]:
    """Load the committed snapshot table (used when no workflow is present)."""
    table = json.loads(FALLBACK_PATH.read_text(encoding="utf-8"))
    return {name: FilterSet(name, patterns) for name, patterns in table.items()}


def filters_for_repo(root: Path | None = None) -> tuple[dict[str, FilterSet], str]:
    """Return (filters, source) for a repository root.

    Source is "live" when the repo carries its own master-ci workflow, or
    "fallback" when the committed snapshot is used.
    """
    if root is not None:
        workflow = root / ".github" / "workflows" / "master-ci.yml"
        if workflow.is_file():
            try:
                return load_filters(workflow), "live"
            except Exception:  # noqa: BLE001 - any parse problem uses the snapshot
                return load_fallback_filters(), "fallback"
    return load_fallback_filters(), "fallback"


def stacks_for_paths(paths: list[str], filters: dict[str, FilterSet]) -> frozenset[str]:
    """Which filters fire for a change set (dorny semantics: any file)."""
    fired: set[str] = set()
    for name, fs in filters.items():
        if any(fs.matches(path) for path in paths):
            fired.add(name)
    return frozenset(fired)


def stacks_for(path: str) -> frozenset[str]:
    return frozenset(name for name, fs in FILTERS.items() if fs.matches(path))


# module-level convenience for the regression suite (this repository)
_REPO = Path(__file__).resolve().parents[1]
_LIVE = _REPO / ".github" / "workflows" / "master-ci.yml"
if _LIVE.is_file():
    FILTERS = load_filters(_LIVE)
else:  # pragma: no cover - only reachable when the module is vendored out
    FILTERS = load_fallback_filters()
