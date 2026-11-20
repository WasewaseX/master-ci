"""Profile inheritance: base -> project -> local chains (roadmap A4).

v2.17.0 shipped the profile as data with one hole: every profile stood
alone. A monorepo wanting an organization-wide baseline (silence the
generated trees once) plus a project-level tweak plus a local
experiment had to copy the base document into each descendant - three
copies that drift independently, the exact failure mode inheritance
exists to remove.

The chain merges least-specific to most-specific, with the merge
precedence documented and test-pinned:

- ``severities`` and ``budgets``: the most specific profile that
    names a key wins. A child's explicit choice overrides its
    parents; a key no child names inherits the nearest ancestor's
    value.
- ``scope_excludes``: the union across the whole chain. Exclusions
    only ever accumulate - a child cannot re-include what a parent
    silenced, which is the same one-directional rule the
    downgrade-only ladder already enforces for severities.

Every document in the chain is loaded through ``load_profile``
(fail-closed: unknown keys, unknown check ids, illegal severities, bad
budgets and unparseable files raise before any merge), and the merged
result is re-validated defensively, so a spec cannot sidestep
validation by being constructed in code. The merged profile is just a
``ProfileSpec``: ``apply_profile`` consumes it unchanged, and no merged
output can escalate anything - both severity values are downgrades by
construction and the merge never introduces a third value.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

from slop_categories import CHECK_CATEGORIES
from slop_profiles import (
    DEFAULT_PROFILE,
    SHIPPED_BUDGETS,
    VALID_SEVERITIES,
    ProfileSpec,
    load_profile,
    validate_scope_glob,
)


def merge_profiles(chain: list[ProfileSpec]) -> ProfileSpec:
    """Merge least-to-most specific; empty chain is the shipped default.

    Severities and budgets are child-wins per key; scope exclusions are
    the union. The result is re-validated against the same registries
    the loader enforces - a defensive check because merge_profiles is a
    public function that accepts constructed specs, not only loaded
    ones, and a hand-built spec that slipped an invalid key past a
    caller must fail loudly here rather than weaken a scan silently.
    """
    if not chain:
        return DEFAULT_PROFILE
    severities: dict[str, str] = {}
    scope_excludes: dict[str, tuple[str, ...]] = {}
    budgets: dict[str, float] = {}
    for spec in chain:
        severities.update(spec.severities)
        budgets.update(spec.budgets)
        for key, patterns in spec.scope_excludes.items():
            existing = scope_excludes.get(key, ())
            scope_excludes[key] = tuple(dict.fromkeys((*existing, *patterns)))
    merged = ProfileSpec(
        name="chain: " + " -> ".join(spec.name for spec in chain),
        description=" merged with ".join(spec.description for spec in chain if spec.description),
        severities=MappingProxyType(severities),
        scope_excludes=MappingProxyType(scope_excludes),
        budgets=MappingProxyType(budgets),
    )
    _validate_merged(merged)
    return merged


def _validate_severities(severities: Mapping[str, str]) -> None:
    """Severity keys are registry ids; values stay in the legal set."""
    for check, severity in severities.items():
        if check not in CHECK_CATEGORIES:
            raise ValueError(f"merged profile: unknown check id '{check}' in severities")
        if severity not in VALID_SEVERITIES:
            raise ValueError(
                f"merged profile: illegal severity {severity!r} for '{check}'"
                f" (valid: {sorted(VALID_SEVERITIES)})"
            )


def _validate_scopes(scope_excludes: Mapping[str, tuple[str, ...]]) -> None:
    """Scope keys are registry ids or '*'; every glob passes the loader's checks."""
    for key, patterns in scope_excludes.items():
        if key != "*" and key not in CHECK_CATEGORIES:
            raise ValueError(f"merged profile: unknown scope key '{key}'")
        for pattern in patterns:
            validate_scope_glob(pattern, f"merged profile: scope '{key}'")


def _validate_budgets(budgets: Mapping[str, float]) -> None:
    """Budget keys are budget-capable ids; values are positive numbers."""
    for check, value in budgets.items():
        if check not in SHIPPED_BUDGETS:
            raise ValueError(
                f"merged profile: unknown budget id '{check}'"
                f" (budget-capable: {sorted(SHIPPED_BUDGETS)})"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"merged profile: budget '{check}' must be positive, got {value!r}")


def _validate_merged(profile: ProfileSpec) -> None:
    """Re-run the registries' invariants over a constructed spec."""
    _validate_severities(profile.severities)
    _validate_scopes(profile.scope_excludes)
    _validate_budgets(profile.budgets)


def load_profile_chain(paths: list[Path]) -> ProfileSpec:
    """Load every document in order and merge; fail-closed throughout.

    Order is least specific first: base, then project, then local. A
    missing file, a malformed document or any validation failure raises
    before the merge - a chain that cannot be fully honored must fail
    the run, never silently weaken the scan (the load_profile rule).
    """
    return merge_profiles([load_profile(path) for path in paths])


def _chain_from_args(raw: list[str | list[str]]) -> list[Path]:
    """Flatten repeated --chain flags, multi-value flags and commas."""
    out: list[Path] = []
    for item in raw:
        values = item if isinstance(item, list) else [item]
        for value in values:
            out.extend(Path(part) for part in str(value).split(",") if part.strip())
    return out


def main(argv: Sequence[str] | None = None, /) -> int:
    """CLI: merge a chain and print the resulting profile document.

    ``--chain`` may be repeated and each value may hold comma-separated
    paths; order is the merge order (base first). ``--json`` prints the
    merged levers as JSON; the default prints the rendered document
    summary. Exit 0 merged, 2 rejected (the configuration-error class).
    """
    parser = argparse.ArgumentParser(
        description="merge an inherited profile chain (base -> project -> local)"
    )
    parser.add_argument(
        "--chain",
        action="append",
        nargs="+",
        default=[],
        help="profile paths, least specific first; repeatable, commas allowed",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    paths = _chain_from_args(args.chain)
    if not paths:
        parser.error("--chain needs at least one profile path")
    try:
        merged = load_profile_chain(paths)
    except (ValueError, OSError) as exc:
        print(f"profile chain rejected: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "name": merged.name,
                    "description": merged.description,
                    "severities": dict(merged.severities),
                    "scope_excludes": {k: list(v) for k, v in merged.scope_excludes.items()},
                    "budgets": dict(merged.budgets),
                },
                indent=2,
            )
        )
    else:
        print(f"profile: {merged.name}")
        for check, severity in merged.severities.items():
            print(f"  severity {check}: {severity}")
        for key, patterns in merged.scope_excludes.items():
            print(f"  scope {key}: {', '.join(patterns)}")
        for check, budget in merged.budgets.items():
            print(f"  budget {check}: {budget}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
