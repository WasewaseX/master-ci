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
from pathlib import Path

from slop_files import read_text_lax
from slop_patterns import SKIP_DIRS
from slop_span_mask import mask_span_forms

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
    can hold thousands of package.json files) and stops after ``cap``
    files, so a monorepo pays a bounded walk for workspace knowledge.
    """
    found: list[Path] = []
    pending = [root]
    while pending and len(found) < cap:
        dirs, named = _split_entries(pending.pop(), names)
        found.extend(named[: cap - len(found)])
        pending.extend(dirs)
    return found


def _split_entries(current: Path, names: frozenset[str]) -> tuple[list[Path], list[Path]]:
    """(descendable directories, named files) of one directory."""
    try:
        entries = sorted(current.iterdir())
    except OSError:
        return [], []
    dirs = [
        entry
        for entry in entries
        if entry.is_dir() and entry.name not in SKIP_DIRS and not entry.name.startswith(".")
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

    tsconfig/jsconfig allow comments; a raw ``json.loads`` refuses them
    and a regex strip would eat ``//`` inside string values, so the
    document is read over the length-preserving mask (double-quoted
    strings only - JSON has no bare-quote literals) before parsing.
    """
    try:
        raw = read_text_lax(path)
    except OSError:
        return None
    try:
        return json.loads(mask_span_forms(raw, quotes='"', keep_strings=True))
    except (json.JSONDecodeError, ValueError):
        return None


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
