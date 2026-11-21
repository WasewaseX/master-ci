"""The js family's declared-set sources: package.json manifests and tsconfig paths.

The undeclared-import check's trust surface. A bare specifier is a
declared package when any ``package.json`` under the root names it - a
dependency section (what it installs) or its own ``name`` (what a
workspace member is importable by) - and the project's own module
layout when a tsconfig/jsconfig ``paths`` prefix renames it. The walks
are bounded and skip the scanner's own skip dirs, so a monorepo pays a
bounded price for workspace knowledge and ``node_modules`` pays none.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from slop_files import read_text_lax
from slop_patterns import SKIP_DIRS
from slop_span_mask import mask_span_forms, string_spans

MAX_JS_MANIFESTS = 64
MAX_ALIAS_CONFIGS = 16
MAX_WALKED_DIRS = 512

_JS_DEP_SECTIONS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


def collect_js_dependencies(root: Path) -> set[str]:
    """Declared npm package names across the tree's package.json files.

    Every package.json under the root counts: its four dependency
    sections declare what it installs, and its own ``name`` field
    declares what a workspace member is importable by - a monorepo
    that imports ``@scope/inner`` from a sibling package is not an
    undeclared import, it is the workspace the root manifest wires up.
    The root manifest alone misses both.
    """
    deps: set[str] = set()
    if not root.is_dir():
        return deps
    for manifest in _bounded_named_files(root, frozenset({"package.json"}), MAX_JS_MANIFESTS):
        deps |= _manifest_names(_read_json(manifest))
    return deps


def _read_json(path: Path) -> object:
    """The parsed document, or None when unreadable or not JSON."""
    try:
        return json.loads(read_text_lax(path))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _manifest_names(data: object) -> set[str]:
    """One manifest's declared names: its own name plus the dep sections."""
    if not isinstance(data, dict):
        return set()
    names: set[str] = set()
    name = data.get("name")
    if isinstance(name, str) and name:
        names.add(name)
    for key in _JS_DEP_SECTIONS:
        table = data.get(key)
        if isinstance(table, dict):
            names.update(entry for entry in table if isinstance(entry, str))
    return names


def _bounded_named_files(root: Path, names: frozenset[str], cap: int) -> list[Path]:
    """Files whose name is in ``names``, pruned and bounded.

    The walk skips the same directories the scanner's file iteration
    skips (``SKIP_DIRS`` plus dot-directories - ``node_modules`` alone
    can hold thousands of package.json files), never re-enters a
    directory it has already visited (two linked folders back to the
    same parent would otherwise revisit the subtree once per link
    path), and stops at ``cap`` files or ``MAX_WALKED_DIRS`` visited
    directories - so a monorepo, and a link cycle, pays a bounded walk
    for workspace knowledge.
    """
    found: list[Path] = []
    pending = [root]
    visited: set[str] = set()
    while pending and len(found) < cap and len(visited) < MAX_WALKED_DIRS:
        current = pending.pop()
        key = os.path.realpath(current)
        if key in visited:
            continue
        visited.add(key)
        dirs, named = _split_entries(current, names)
        found.extend(named[: cap - len(found)])
        pending.extend(dirs)
    return found


def _split_entries(current: Path, names: frozenset[str]) -> tuple[list[Path], list[Path]]:
    """(descendable directories, named files) of one directory.

    A directory symlink never descends: a link back to an ancestor
    would turn the walk into a cycle, and a link to a sibling subtree
    would read its manifests once per link path.
    """
    try:
        entries = sorted(current.iterdir())
    except OSError:
        return [], []
    dirs = [
        entry
        for entry in entries
        if entry.is_dir()
        and not entry.is_symlink()
        and entry.name not in SKIP_DIRS
        and not entry.name.startswith(".")
    ]
    named = [entry for entry in entries if entry.is_file() and entry.name in names]
    return dirs, named


def collect_js_alias_prefixes(root: Path) -> frozenset[str]:
    """tsconfig/jsconfig ``paths`` prefixes under root (bounded walk).

    A bare specifier a path alias renames - ``@app/components/x`` - is
    the project's own module layout, not an npm package, and flagging
    it would be a false positive on every aliased import the project
    writes. The prefixes are the declaration that keeps those quiet.
    """
    prefixes: set[str] = set()
    for config in _bounded_named_files(
        root, frozenset({"tsconfig.json", "jsconfig.json"}), MAX_ALIAS_CONFIGS
    ):
        prefixes |= _alias_prefixes(_read_jsonc(config))
    return frozenset(prefixes)


def _read_jsonc(path: Path) -> object:
    """The parsed config, comments masked out; None when unparseable.

    tsconfig/jsconfig allow comments and trailing commas; a raw
    ``json.loads`` refuses both and a regex strip would eat ``//``
    inside string values, so the document is read over the
    length-preserving mask (double-quoted strings only - JSON has no
    bare-quote literals), then trailing commas outside strings are
    dropped before parsing. A config that still will not parse
    contributes nothing - an advisory check never guesses.
    """
    try:
        raw = read_text_lax(path)
    except OSError:
        return None
    try:
        masked = mask_span_forms(raw, quotes='"', keep_strings=True)
        return json.loads(_strip_trailing_commas(masked))
    except (json.JSONDecodeError, ValueError):
        return None


def _strip_trailing_commas(masked: str) -> str:
    """The document with trailing commas outside strings removed.

    tsconfig's JSON dialect allows ``{"paths": {"@app/*": ["./src/*"],}}``;
    a strict ``json.loads`` refuses the comma and the config's real
    aliases would be reported as undeclared packages instead. The
    string spans come from the same masked copy (its strings are kept,
    its comments are blanks), so a comma the reader cannot prove is
    outside a literal stays, and everything else parses as written.
    """
    spans = string_spans(masked)
    out: list[str] = []
    index = 0
    while index < len(masked):
        char = masked[index]
        if char == "," and not _inside_spans(index, spans):
            look = _next_significant(masked, index + 1)
            if look < len(masked) and masked[look] in "}]":
                out.extend(masked[index + 1 : look])
                index = look
                continue
        out.append(char)
        index += 1
    return "".join(out)


def _inside_spans(index: int, spans: list[tuple[int, int]]) -> bool:
    """True when ``index`` sits inside a string literal's span."""
    return any(start < index < end for start, end in spans)


def _next_significant(masked: str, start: int) -> int:
    """The first index at or after ``start`` that is not whitespace."""
    look = start
    while look < len(masked) and masked[look] in " \t\r\n":
        look += 1
    return look


def _alias_prefixes(data: object) -> set[str]:
    """The ``compilerOptions.paths`` prefixes one parsed config declares.

    ``"@app/*": [...]`` contributes ``@app``; an exact key
    ``"$lib": [...]`` contributes itself. A config that will not parse
    contributes nothing - an advisory check never guesses.
    """
    if not isinstance(data, dict):
        return set()
    compiler = data.get("compilerOptions")
    if not isinstance(compiler, dict):
        return set()
    paths = compiler.get("paths")
    if not isinstance(paths, dict):
        return set()
    return {key.removesuffix("/*") for key in paths if isinstance(key, str) and key}
