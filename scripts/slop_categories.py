"""Quality-dimension taxonomy: which bucket each check id reports under.

The AI-integrity score and the code-quality dimensions are deliberately
separate (v1.18 review guidance): a repo can be slop-free and still
unhealthy, and a hand-written repo can be healthy and still full of
machine prose. Mixing them into one number makes both signals less
credible, so every check id is explicitly categorized:

- architecture       the repo's declared import law (opt-in config)
- code-health        defects a reviewer fixes before merge
- reliability        failure paths: error handling, leaks, shared state (v2.15)
- maintainability    design debt, hygiene, suppression habits
- security           injection surfaces and credential handling
- test-assurance     tests that can fail, and coverage of the repo
- dependency-health  manifest vs. reality (declared, used, hallucinated)
- ai-integrity       authorship and machine-prose signals (the slop gate)

The default for an unmapped id is ai-integrity, but the contract test
(``test_every_check_has_a_category``) requires every REMEDIES key to be
mapped explicitly, so a new check must state its bucket on launch.
"""

from __future__ import annotations

CATEGORY_ORDER = (
    "architecture",
    "code-health",
    "reliability",
    "maintainability",
    "security",
    "test-assurance",
    "dependency-health",
    "ai-integrity",
)

CATEGORY_LABELS = {
    "architecture": "Architecture",
    "code-health": "Code health",
    "reliability": "Reliability",
    "maintainability": "Maintainability",
    "security": "Security",
    "test-assurance": "Test assurance",
    "dependency-health": "Dependency health",
    "ai-integrity": "AI integrity",
}

CHECK_CATEGORIES: dict[str, str] = {
    # Architecture: the declared law, enforced. Its own dimension because
    # a boundary violation is not a smell - it breaks the stated design.
    "arch-forbidden-import": "architecture",
    "arch-layer-violation": "architecture",
    "arch-missing-pattern": "architecture",
    # Reliability (v2.15): the failure-path dimension, no longer blended
    # into code health - error handling, resource leaks, shared state.
    # Membership mirrors slop_reliability.RELIABILITY_CHECKS exactly, so
    # the dimension table and the rating can never disagree.
    "bare-except": "reliability",
    "swallowed-exception": "reliability",
    "empty-catch": "reliability",
    "empty-rescue": "reliability",
    "go-ignored-error": "reliability",
    "java-broad-throws": "reliability",
    "redundant-try-catch": "reliability",
    "silent-recovery": "reliability",
    "unwrap-density": "reliability",
    "mutable-default": "reliability",
    "unclosed-resource": "reliability",
    "shared-mutable-state": "reliability",
    "reliability-rating": "reliability",
    # Code health: defects a reviewer fixes before merge, whatever wrote them.
    "go-error-assigned-blank": "code-health",
    "print-stacktrace-only": "code-health",
    "debugger-left": "code-health",
    "debug-print": "code-health",
    "unreachable-code": "code-health",
    "constant-condition": "code-health",
    "boilerplate-duplication": "code-health",
    "duplicate-import": "code-health",
    "phantom-import": "code-health",
    "bloat-long-file": "code-health",
    "bloat-comment-ratio": "code-health",
    "bloat-banner-comments": "code-health",
    "god-function": "code-health",
    "deep-nesting": "code-health",
    "chained-dict-get": "code-health",
    "todo-density": "code-health",
    "unused-declaration": "code-health",
    "orphan-module": "code-health",
    # Maintainability: design debt and hygiene, judged per codebase.
    "bloat-param-count": "maintainability",
    "high-cyclomatic": "maintainability",
    "high-cognitive": "maintainability",
    "star-import": "maintainability",
    "range-len-loop": "maintainability",
    "isinstance-ladder": "maintainability",
    "repetitive-dispatch": "maintainability",
    "generic-name": "maintainability",
    "thin-wrapper": "maintainability",
    "hardcoded-id": "maintainability",
    "hardcoded-url": "maintainability",
    "unknown-directive": "maintainability",
    "lint-escape": "maintainability",
    "ts-suppression": "maintainability",
    "blanket-suppression": "maintainability",
    # v2.0: the module dependency graph. An undeclared cycle is design
    # debt, not a declared-law violation - maintainability, not architecture.
    "import-cycle": "maintainability",
    # v2.12: class cohesion (LCOM4). A disconnected method graph is design
    # debt the reviewer judges - the split is a design decision.
    "low-cohesion": "maintainability",
    # v2.13: the project densities. A ratio is a trend the ratchet holds,
    # not a defect to fix mechanically - maintainability, advisory.
    "complexity-density": "maintainability",
    "duplication-density": "maintainability",
    # v2.14: remediation debt (the priced scan) and module coupling
    # (fan-out, instability). Ratios and texture, not defects -
    # design conversations, maintainability, advisory.
    "debt-ratio": "maintainability",
    "high-coupling": "maintainability",
    "unstable-hub": "maintainability",
    # v2.15: the hotspot ranking (churn x complexity x defects x
    # dependents) and the abstraction texture (forwarding chains,
    # no-behavior classes) - prioritization and design texture, not
    # defects. Maintainability, advisory.
    "hotspot-risk": "maintainability",
    "forwarding-chain": "maintainability",
    "no-behavior-class": "maintainability",
    # Security: injection surfaces and credential handling.
    "eval-usage": "security",
    "placeholder-credential": "security",
    # Test assurance: tests that cannot fail, or that do not exist.
    "empty-test": "test-assurance",
    "tautological-test": "test-assurance",
    "missing-tests": "test-assurance",
    "low-new-coverage": "test-assurance",
    # Dependency health: the manifest against the imports.
    "undeclared-import": "dependency-health",
    "unused-dependency": "dependency-health",
    # AI integrity: authorship and machine-prose signals. This is the
    # dimension the --max-slop-score gate measures; everything stylistic
    # or speculative about machine-written content lives here.
    "ai-vocab": "ai-integrity",
    "emoji-comments": "ai-integrity",
    "trivial-comments": "ai-integrity",
    "elided-code": "ai-integrity",
    "placeholder-impl": "ai-integrity",
    "placeholder-note": "ai-integrity",
    "stub-function": "ai-integrity",
    "empty-function": "ai-integrity",
    "rust-stub": "ai-integrity",
    "chatbot-residue": "ai-integrity",
    "ai-prose": "ai-integrity",
    "humanizer": "ai-integrity",
    "em-dash-density": "ai-integrity",
    "rule-of-three": "ai-integrity",
    "rhetorical-questions": "ai-integrity",
    "exclamations": "ai-integrity",
    "ai-provenance": "ai-integrity",
    "md-emoji-headings": "ai-integrity",
    "md-residue": "ai-integrity",
    "step-comments": "ai-integrity",
    "meta-comment": "ai-integrity",
    "prose-block": "ai-integrity",
    "placeholder-naming": "ai-integrity",
    "ui-eyebrow-label": "ai-integrity",
    "ui-italic-serif-hero": "ai-integrity",
    "ui-fake-stats": "ai-integrity",
    "ui-generic-testimonial": "ai-integrity",
    "ui-not-just-x": "ai-integrity",
    "ui-triple-slogan": "ai-integrity",
    "ui-glass-glow": "ai-integrity",
    "ui-decorative-numbering": "ai-integrity",
    "ui-ai-gradient": "ai-integrity",
    "ui-gradient-text": "ai-integrity",
    "ui-marketing-copy": "ai-integrity",
    "ui-placeholder-copy": "ai-integrity",
    "ui-emoji-decor": "ai-integrity",
    "ui-placeholder-image": "ai-integrity",
    "ui-identical-cards": "ai-integrity",
    "ui-em-dash-overuse": "ai-integrity",
}


def category_of(check: str) -> str:
    """Bucket for one check id; unmapped ids stay in the AI-integrity bucket."""
    return CHECK_CATEGORIES.get(check, "ai-integrity")
