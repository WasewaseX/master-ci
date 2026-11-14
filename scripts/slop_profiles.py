"""Analyzer profiles: per-check severities, scope exclusions and budget overrides.

The roadmap v2.17 P2 row "Analyzer profiles" asks for Qodana-style
per-language rules, severities, scopes and thresholds; the verified state
today is "Thresholds are global constants". Every budget and severity in
the engine is a module constant that all consumers inherit wholesale, so
a monorepo with legitimately generated trees, or a team that judges one
noisy check inadmissible, has no lever short of forking the thresholds.
A profile is that lever, as data: one JSON (or YAML) document naming
check ids from the one registry (slop_categories.CHECK_CATEGORIES) -
never a second, private id list.

Downgrade-only is the load-bearing rule. A profile relaxes noise; it
never invents severity. The ladder is blocking > advisory > off, and
apply_profile only walks it downward: a blocking finding may drop one
grade to advisory (the check still reports, it stops failing builds), a
finding may be silenced entirely by the severity "off" or by a scope
glob, and no code path in this module can raise advisory-ness - the
escalation direction would let a config file re-grade a defect the
check registry called blocking. Every silenced finding is returned as a
Suppression naming the reason, so a profile never deletes silently.

Scope globs reuse the path_filters translator (dorny/picomatch
semantics: ``**/`` matches zero directories, ``*`` never crosses ``/``,
brace groups expand), so a profile scope and a workflow path filter read
alike. Budget overrides are keyed to the check ids the engines actually
price with budgets, bound to the live module constants in SHIPPED_BUDGETS
below - imported, never copied. The identity contract in
tests/test_profiles.py pins those values, so a silent budget retune
fails loudly instead of drifting under the profile layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from path_filters import FilterSet, UnsupportedPatternError
from slop_categories import CHECK_CATEGORIES
from slop_coupling import COUPLING_FAN_OUT_BUDGET
from slop_density import COMPLEXITY_DENSITY_BUDGET, DUP_DENSITY_BUDGET
from slop_hotspot import HOTSPOT_RISK_BUDGET
from slop_model import Finding

VALID_SEVERITIES = frozenset({"advisory", "off"})
_TOP_KEYS = frozenset({"name", "description", "severities", "scope_excludes", "budgets"})
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})

# The budget-capable checks and their shipped thresholds, bound to the
# live constants of the owning modules. density_findings consumes the
# density pair today; the coupling and hotspot bindings stand for the
# identity contract and the engines' next budget parameters.
SHIPPED_BUDGETS: Mapping[str, float] = MappingProxyType(
    {
        "complexity-density": COMPLEXITY_DENSITY_BUDGET,
        "duplication-density": DUP_DENSITY_BUDGET,
        "high-coupling": COUPLING_FAN_OUT_BUDGET,
        "hotspot-risk": HOTSPOT_RISK_BUDGET,
    }
)

_NO_SEVERITIES: Mapping[str, str] = MappingProxyType({})
_NO_SCOPES: Mapping[str, tuple[str, ...]] = MappingProxyType({})
_NO_BUDGETS: Mapping[str, float] = MappingProxyType({})


@dataclass(frozen=True)
class Suppression:
    """One finding a profile silenced, with the visible reason why."""

    check: str
    path: str
    reason: str


@dataclass(frozen=True)
class ProfileSpec:
    """One profile's downgrade-only levers, held as immutable data.

    severities maps a check id to "advisory" (one grade down) or "off"
    (silenced everywhere); scope_excludes maps a check id or "*" to path
    globs that silence matching findings; budgets maps a budget-capable
    check id to its replacement threshold. Empty mappings mean "the
    shipped behavior stands", which is exactly DEFAULT_PROFILE.
    """

    name: str
    description: str = ""
    severities: Mapping[str, str] = _NO_SEVERITIES
    scope_excludes: Mapping[str, tuple[str, ...]] = _NO_SCOPES
    budgets: Mapping[str, float] = _NO_BUDGETS


DEFAULT_PROFILE = ProfileSpec(
    name="default",
    description="No levers: registry severities, no scopes, shipped budgets.",
)


@lru_cache(maxsize=None)
def _compiled(pattern: str) -> FilterSet:
    """One scope glob, compiled once through the shared path_filters translator."""
    return FilterSet(pattern, [pattern])


def _matches(pattern: str, path: str) -> bool:
    """Dorny-style scope match; the repo-level anchor "." matches no glob."""
    if not path or path == ".":
        return False
    return _compiled(pattern).matches(path.replace("\\", "/"))


def _off_reason(profile_name: str) -> str:
    return f"profile '{profile_name}': check severity off"


def _scope_reason(profile_name: str, pattern: str) -> str:
    return f"profile '{profile_name}': scope '{pattern}'"


def _scope_hit(profile: ProfileSpec, check: str, rel_path: str, finding_path: str) -> str | None:
    """The first scope glob covering the finding (own path or caller rel_path)."""
    for key in (check, "*"):
        for pattern in profile.scope_excludes.get(key, ()):
            if _matches(pattern, rel_path) or _matches(pattern, finding_path):
                return pattern
    return None


def apply_profile(
    findings: list[Finding],
    profile: ProfileSpec,
    rel_path: str,
) -> tuple[list[Finding], list[Suppression]]:
    """Downgrade or silence findings per the profile; report what moved.

    Returns (kept findings, suppressions), input order preserved and the
    input list never mutated. Severity moves are downgrade-only:
    "advisory" demotes a blocking finding one grade - a copy with
    advisory set after construction, because Finding.__post_init__
    resolves advisory-ness from the registry and the profile's explicit
    decision applies on top of it - while "off" and scope hits move the
    finding into a Suppression naming the reason. Nothing here can raise
    advisory-ness: a profile relaxes noise, it never invents severity.
    Scope globs match the finding's own recorded path and the caller's
    rel_path (the scan-relative path of the unit the findings came
    from; a whole-report call passes "."), so per-file and whole-report
    application behave alike and repo-level findings anchored at "."
    can still be scoped.
    """
    kept: list[Finding] = []
    suppressed: list[Suppression] = []
    for finding in findings:
        severity = profile.severities.get(finding.check)
        if severity == "off":
            suppressed.append(
                Suppression(
                    check=finding.check, path=finding.path, reason=_off_reason(profile.name)
                )
            )
            continue
        pattern = _scope_hit(profile, finding.check, rel_path, finding.path)
        if pattern is not None:
            suppressed.append(
                Suppression(
                    check=finding.check,
                    path=finding.path,
                    reason=_scope_reason(profile.name, pattern),
                )
            )
            continue
        if severity == "advisory" and not finding.advisory:
            downgraded = replace(finding)
            downgraded.advisory = True  # the one legal move: one grade down
            kept.append(downgraded)
            continue
        kept.append(finding)
    return kept, suppressed


def budget_override(profile: ProfileSpec, check_id: str) -> float | None:
    """The profile's replacement budget for a check id, or None to keep shipped."""
    return profile.budgets.get(check_id)


def shipped_budget(check_id: str) -> float | None:
    """The live shipped budget a check id prices at when no profile overrides it."""
    return SHIPPED_BUDGETS.get(check_id)


def load_profile(path: Path) -> ProfileSpec:
    """Read one profile document; every violation fails closed.

    JSON is always supported; a .yaml/.yml suffix routes through pyyaml
    when it is importable (the path_filters/tool_versions discipline)
    and raises with a direction when it is not. A malformed document, an
    unknown key, an unknown check id, an illegal severity or a bad
    budget raises ValueError naming the offending key and the valid set
    - a profile that cannot be honored must fail the run, never
    silently weaken the scan.
    """
    data = _parse_document(path, path.read_text(encoding="utf-8"))
    _reject_unknown_keys(path, data)
    name = _require_name(path, data)
    description = _require_description(path, data)
    return ProfileSpec(
        name=name,
        description=description,
        severities=MappingProxyType(_severities(path, data.get("severities", {}))),
        scope_excludes=MappingProxyType(_scopes(path, data.get("scope_excludes", {}))),
        budgets=MappingProxyType(_budgets(path, data.get("budgets", {}))),
    )


def _parse_document(path: Path, text: str) -> dict[str, object]:
    """One profile document as a dict: JSON, or suffix-routed YAML."""
    if path.suffix.lower() in _YAML_SUFFIXES:
        try:
            import yaml  # type: ignore[import-untyped]  # local import: pyyaml is optional
        except ImportError as exc:
            raise ValueError(
                f"profile {path}: YAML profiles need pyyaml; use JSON or install pyyaml"
            ) from exc
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"malformed profile {path}: {exc}") from exc
    else:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed profile {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"malformed profile {path}: top level must be an object")
    return parsed


def _reject_unknown_keys(path: Path, data: Mapping[str, object]) -> None:
    """A typo'd lever must fail loudly, not silently no-op."""
    unknown = sorted(str(key) for key in data if key not in _TOP_KEYS)
    if unknown:
        raise ValueError(
            f"profile {path}: unknown key(s) {unknown}; valid keys: {sorted(_TOP_KEYS)}"
        )


def _require_name(path: Path, data: Mapping[str, object]) -> str:
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"profile {path}: 'name' must be a non-empty string")
    return name


def _require_description(path: Path, data: Mapping[str, object]) -> str:
    description = data.get("description", "")
    if not isinstance(description, str):
        raise ValueError(f"profile {path}: 'description' must be a string")
    return description


def _severities(path: Path, raw: object) -> dict[str, str]:
    """Validate the severity map: registry check ids, downgrade-only values."""
    if not isinstance(raw, dict):
        raise ValueError(f"profile {path}: 'severities' must map check id to severity")
    out: dict[str, str] = {}
    for check, severity in raw.items():
        if not isinstance(check, str) or check not in CHECK_CATEGORIES:
            raise ValueError(
                f"profile {path}: unknown check id '{check}' in 'severities'; "
                "valid ids are the keys of slop_categories.CHECK_CATEGORIES"
            )
        if severity not in VALID_SEVERITIES:
            raise ValueError(
                f"profile {path}: illegal severity {severity!r} for check '{check}'; "
                f"valid severities: {sorted(VALID_SEVERITIES)} (downgrade-only, "
                "a profile never escalates)"
            )
        out[check] = severity
    return out


def _scopes(path: Path, raw: object) -> dict[str, tuple[str, ...]]:
    """Validate the scope map: registry ids or '*', non-empty glob lists."""
    if not isinstance(raw, dict):
        raise ValueError(f"profile {path}: 'scope_excludes' must map check id or '*' to globs")
    out: dict[str, tuple[str, ...]] = {}
    for key, patterns in raw.items():
        if not isinstance(key, str) or (key != "*" and key not in CHECK_CATEGORIES):
            raise ValueError(
                f"profile {path}: unknown scope key '{key}'; valid keys: '*' "
                "or the keys of slop_categories.CHECK_CATEGORIES"
            )
        if not isinstance(patterns, list) or not patterns:
            raise ValueError(f"profile {path}: scope '{key}' must be a non-empty list of globs")
        out[key] = tuple(_scope_pattern(path, key, pattern) for pattern in patterns)
    return out


def _scope_pattern(path: Path, key: str, pattern: object) -> str:
    """One validated scope glob: non-empty, include-only, compilable."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError(f"profile {path}: scope '{key}' globs must be non-empty strings")
    if pattern.startswith("!"):
        raise ValueError(
            f"profile {path}: scope '{key}' glob '{pattern}': scopes are include-only, "
            "a negation would silently re-include what another glob silenced"
        )
    try:
        _compiled(pattern)
    except UnsupportedPatternError as exc:
        raise ValueError(
            f"profile {path}: scope '{key}' glob '{pattern}' is not a supported pattern: {exc}"
        ) from exc
    return pattern


def _budgets(path: Path, raw: object) -> dict[str, float]:
    """Validate the budget map: budget-capable ids, positive numbers."""
    if not isinstance(raw, dict):
        raise ValueError(f"profile {path}: 'budgets' must map check id to a positive number")
    out: dict[str, float] = {}
    for check, value in raw.items():
        if not isinstance(check, str) or check not in SHIPPED_BUDGETS:
            raise ValueError(
                f"profile {path}: unknown budget id '{check}'; budget overrides "
                f"exist only for {sorted(SHIPPED_BUDGETS)}"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(
                f"profile {path}: budget '{check}' must be a positive number, got {value!r}"
            )
        out[check] = float(value)
    return out


def profile_from_args(args: argparse.Namespace) -> ProfileSpec | None:
    """The requested analyzer profile for a parsed CLI namespace.

    None (after the reject line on stderr) when the profile cannot be
    honored - the configuration-error class, exit 2 at the call site,
    raised before the scan is spent. Lives here rather than in the CLI
    module so the CLI stays inside the same 400-line budget it enforces
    on consumers (the slop_cli split precedent).
    """
    try:
        return load_profile(args.profile) if args.profile else DEFAULT_PROFILE
    except (ValueError, OSError) as exc:
        print(f"ai-integrity: profile rejected: {exc}", file=sys.stderr)
        return None


def apply_profile_stage(findings: list, profile: ProfileSpec) -> tuple[list, list[Suppression]]:
    """Apply the profile to the scanned report and name every silence.

    Every suppressed finding is printed one per line with its reason: a
    profile relaxes noise, it never deletes it silently. Returns the
    profiled report and the suppression records for the run summary.
    """
    kept, suppressed = apply_profile(findings, profile, ".")
    for item in suppressed:
        print(f"profile: suppressed {item.check} at {item.path}: {item.reason}")
    return kept, suppressed
