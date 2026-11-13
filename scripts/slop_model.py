"""Finding/Comment model, remedies, check classes and the shared finding helper."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from slop_patterns import _line_of
from slop_remedies import REMEDIES  # noqa: F401 - re-exported registry surface

BLOCKING_CHECKS = frozenset(
    {
        "ai-vocab",
        "emoji-comments",
        "todo-density",
        "trivial-comments",
        "boilerplate-duplication",
        "elided-code",
        "placeholder-impl",
        "stub-function",
        "empty-test",
        "bare-except",
        "swallowed-exception",
        "empty-catch",
        "empty-rescue",
        "go-ignored-error",
        "print-stacktrace-only",
        "debugger-left",
        "rust-stub",
        "chatbot-residue",
        # v2.15: a resource leak is a defect a reviewer fixes before merge,
        # the same grade as the other reliability defects.
        "unclosed-resource",
        # Opt-in architecture rules: a declared rule is a contract, not a
        # judgment call - violations are blocking-grade by construction.
        "arch-forbidden-import",
        "arch-layer-violation",
        "arch-missing-pattern",
    }
)

# The structural-budget family: the standards the detector holds its own
# codebase to. Advisory by default (budget discipline is a judgment call
# for most codebases), but escalatable to blocking by an explicit
# enforcement decision (--enforce-bloat, or the strict CI profile).
STRUCTURAL_BUDGET_CHECKS = frozenset(
    {
        "bloat-long-file",
        "bloat-param-count",
        "bloat-comment-ratio",
        "bloat-banner-comments",
        "god-function",
        "deep-nesting",
        "chained-dict-get",
        "high-cyclomatic",
        "high-cognitive",
    }
)

# Mutated by the CLI (--enforce-bloat) BEFORE scanning, so every Finding
# resolves escalated advisory-ness in __post_init__ and scoring, SARIF
# and the report table agree on severity.
ENFORCED_CHECKS: set[str] = set()


def enforce_structural_budget() -> None:
    """Escalate the structural-budget family to blocking-grade severity."""
    ENFORCED_CHECKS.update(STRUCTURAL_BUDGET_CHECKS)


def is_blocking(check: str) -> bool:
    """True when a finding for `check` feeds the blocking score."""
    return check in BLOCKING_CHECKS or check in ENFORCED_CHECKS


# Judgment-call checks: a finding here can never fail a build on its own,
# no matter how a call site constructs the Finding - unless the check is
# also in ENFORCED_CHECKS (see is_blocking / __post_init__ below).
ADVISORY_ONLY_CHECKS = frozenset(
    {
        "god-function",
        "deep-nesting",
        "high-cyclomatic",
        "high-cognitive",
        "mutable-default",
        "star-import",
        "unwrap-density",
        "ai-provenance",
        "go-error-assigned-blank",
        "md-residue",
        "phantom-import",
        "placeholder-credential",
        "placeholder-note",
        "ts-suppression",
        "undeclared-import",
        "bloat-long-file",
        "bloat-param-count",
        "bloat-comment-ratio",
        "bloat-banner-comments",
        "chained-dict-get",
        # scanaislop round-2 adoption: dead residue, dispatch shapes,
        # comment prose, naming and environment leakage. Judgment calls.
        "unreachable-code",
        "constant-condition",
        "range-len-loop",
        "isinstance-ladder",
        "repetitive-dispatch",
        "duplicate-import",
        "redundant-try-catch",
        "silent-recovery",
        "tautological-test",
        "generic-name",
        "meta-comment",
        "prose-block",
        "hardcoded-url",
        # scanaislop round-3 adoption: forwarding shells, empty bodies,
        # baked record ids, invented suppressions. Judgment calls.
        "thin-wrapper",
        "empty-function",
        "hardcoded-id",
        "unknown-directive",
        "ui-eyebrow-label",
        "ui-italic-serif-hero",
        "ui-fake-stats",
        "ui-generic-testimonial",
        "ui-not-just-x",
        "ui-triple-slogan",
        "ui-glass-glow",
        "ui-decorative-numbering",
        # scanaislop round-4 adoption: repo-level deadness. Only a name
        # whose occurrences are exactly its own definition is reported.
        "unused-declaration",
        "unused-dependency",
        # v2.1: the file-scale deadness oracle. A module is reported only
        # when no import, config, doc or script mentions it anywhere.
        "orphan-module",
        # v2.0: the module dependency graph. Cycles are design debt with
        # legitimate lazy-loading escape hatches, so the finding advises.
        "import-cycle",
        # scanaislop round-5 adoption (languages page): broad Java throws.
        "java-broad-throws",
        # scanaislop round-6 adoption: dynamic evaluation and the
        # placeholder-image / identical-cards / em-dash design tells.
        "eval-usage",
        "ui-placeholder-image",
        "ui-identical-cards",
        "ui-em-dash-overuse",
        # v1.21 roadmap item 4: measured test assurance. The gate itself
        # fails the build below the floor; the per-file findings stay
        # advisory so the dimension table carries the detail.
        "low-new-coverage",
        # v2.12 roadmap P1 item 1: class cohesion, measured. LCOM4 over
        # connected method groups is an advisory measurement with the
        # groups as evidence - the split is a design decision, not a
        # mechanical defect, so it advises like the other budgets.
        "low-cohesion",
        # v2.13 roadmap P1 item 2: the project densities. A ratio judges
        # the codebase as a whole, not one defect - evidence for a
        # refactor conversation, advisory like the other budgets.
        "complexity-density",
        "duplication-density",
        # v2.14: remediation debt and module coupling - priced and
        # textured design evidence, advisory like the other budgets.
        "debt-ratio",
        "high-coupling",
        "unstable-hub",
        # v2.15: the reliability dimension speaks in grades, the hotspot
        # ranks the corpus, the abstraction checks judge chains and
        # shells - judgment calls, advisory like every other budget.
        "reliability-rating",
        "shared-mutable-state",
        "hotspot-risk",
        "forwarding-chain",
        "no-behavior-class",
    }
)


@dataclass
class Finding:
    path: str
    line: int
    check: str
    message: str
    weight: int
    advisory: bool = field(default=False)

    def __post_init__(self) -> None:
        if is_blocking(self.check):
            self.advisory = False
        elif self.check in ADVISORY_ONLY_CHECKS:
            self.advisory = True

    @property
    def remedy(self) -> str:
        return REMEDIES.get(self.check, "Review and fix manually")


@dataclass
class Comment:
    line: int
    text: str
    kind: str  # 'line' | 'block' | 'docstring'


def _finding(
    path: Path,
    text: str,
    pos: int,
    check: str,
    message: str,
    weight: int,
) -> Finding:
    # advisory-ness is registry data (BLOCKING_CHECKS / ADVISORY_ONLY_CHECKS),
    # resolved by Finding.__post_init__ - not a per-call-site flag.
    return Finding(
        path=str(path),
        line=_line_of(text, pos),
        check=check,
        message=message,
        weight=weight,
    )
