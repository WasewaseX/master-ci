"""Finding/Comment model, remedies, check classes and the shared finding helper."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from slop_patterns import _line_of

REMEDIES = {
    "ai-vocab": "Rewrite the comment to describe intent in plain words",
    "emoji-comments": "Remove decorative emoji from code comments",
    "todo-density": "Resolve, ticket or delete the TODO/FIXME markers",
    "trivial-comments": "Delete comments that restate the code; explain why instead",
    "boilerplate-duplication": "Extract the repeated lines into a shared helper",
    "bloat-long-file": (
        "Split the file by responsibility; ask for the version that does "
        "the same job in fewer lines"
    ),
    "bloat-param-count": (
        "Group the parameters into an options object or struct; more than "
        "six parameters is unreadable at the call site"
    ),
    "bloat-comment-ratio": (
        "Delete comments that narrate the code; keep only the ones that "
        "explain intent or constraints"
    ),
    "bloat-banner-comments": (
        "Remove decorative banner comments; section names belong to the structure, not to ASCII art"
    ),
    "chained-dict-get": (
        "Normalize the input at the boundary or split the lookup; chained "
        ".get(..., {}) defaults hide missing-data cases"
    ),
    "ui-eyebrow-label": ("Drop the decorative eyebrow label; let the headline carry the section"),
    "ui-italic-serif-hero": ("Set headlines in the brand face without the italic-word gesture"),
    "ui-fake-stats": (
        "Cite the source of every figure or remove it; invented stats are a generic-AI tell"
    ),
    "ui-generic-testimonial": (
        "Attribute testimonials with a full name and a real profile; "
        "first-name-plus-role is stock-slop"
    ),
    "ui-not-just-x": ("State what the product does; 'not just X, it's Y' fits any competitor"),
    "ui-triple-slogan": ("Replace the three-word slogan with concrete copy"),
    "ui-glass-glow": (
        "Use glass and glow effects sparingly; layered backdrop blur is the AI-default aesthetic"
    ),
    "ui-decorative-numbering": (
        "Remove decorative 01/02/03 numbering; reading order already numbers the sections"
    ),
    "elided-code": "Restore the omitted code; elided edits silently delete working code",
    "placeholder-impl": "Implement the body or delete the function; stubs look done but are not",
    "placeholder-note": "Replace the template note with the real value or implementation",
    "stub-function": "Implement the stub or remove it; list it in a TODO ticket instead",
    "empty-test": "Add assertions; a test that cannot fail proves nothing",
    "bare-except": "Catch the specific exception and handle or re-raise it",
    "swallowed-exception": "Log, wrap or re-raise; a pass-only handler hides failures",
    "empty-catch": "Handle the error or propagate it; an empty catch hides bugs",
    "empty-rescue": "Handle the specific error class or re-raise",
    "go-ignored-error": "Return or wrap the error; returning nil hides the failure",
    "go-error-assigned-blank": "Handle or comment why the error is deliberately discarded",
    "print-stacktrace-only": "Log with context via your logger instead of printStackTrace",
    "debugger-left": "Remove the debugger statement or breakpoint before committing",
    "debug-print": "Remove the debug print or use structured logging",
    "phantom-import": "Delete the import or use it; unused imports are dead weight",
    "undeclared-import": (
        "Verify the package exists and declare it in the manifest; "
        "hallucinated names get squatted by attackers (slopsquatting)"
    ),
    "lint-escape": "Fix the underlying issue or scope the suppression to one rule and line",
    "ts-suppression": "Fix the type instead of suppressing; avoid any",
    "blanket-suppression": "Scope the suppression to a single rule with a reason",
    "step-comments": "Let the code read like code; move narration to the PR description",
    "placeholder-naming": "Rename placeholder identifiers to names that describe the data",
    "placeholder-credential": "Load credentials from the environment or a secret store",
    "god-function": "Split the function along its responsibilities",
    "deep-nesting": "Guard-clause and extract helpers to flatten the nesting",
    "unwrap-density": "Handle the Result/Option; unwrap panics in production",
    "rust-stub": "Implement the function; todo!() panics at runtime",
    "mutable-default": "Default to None and build the container inside the function",
    "star-import": "Import the names you use; star imports hide origins",
    "ui-ai-gradient": "Use the project's design tokens; the purple-pink gradient is the AI default",
    "ui-gradient-text": "Gradient headlines are the template aesthetic; use solid brand color",
    "ui-marketing-copy": "Replace buzzwords with what the product actually does",
    "ui-placeholder-copy": "Replace lorem/company placeholders with real copy",
    "ui-emoji-decor": "Use the icon set, not emoji, for feature decoration",
    "md-emoji-headings": "Keep headings text-only; emoji headings are a generated-doc tell",
    "md-residue": "Delete chatbot residue and placeholders from the document",
    "chatbot-residue": "Remove assistant-voice phrases from comments",
    "ai-prose": "Rewrite the passage in your own words",
    "humanizer": "Remove the filler phrase",
    "em-dash-density": "Use commas, colons or parentheses instead of em-dashes",
    "rule-of-three": "Vary list lengths; uniform triples are a machine-prose tell",
    "rhetorical-questions": "State the answer instead of asking the reader",
    "exclamations": "Remove exclamation marks from documentation",
    "ai-provenance": (
        "Declare the generating tool in the commit message or PR description, "
        "or remove the stale marker"
    ),
    "unreachable-code": "Delete the dead statements; code that can never run does not ship",
    "constant-condition": (
        "Remove the dead branch or make the condition real; constants gate nothing"
    ),
    "range-len-loop": ("Iterate the sequence directly, or use enumerate when the index is needed"),
    "isinstance-ladder": ("Replace the hand-rolled type dispatch with a mapping or polymorphism"),
    "repetitive-dispatch": ("Replace the ==-chain with a dict dispatch or a match statement"),
    "duplicate-import": "Merge the duplicate imports into one statement",
    "redundant-try-catch": "Drop the try wrapper or handle the error for real",
    "silent-recovery": (
        "Include the caught error in the log and decide: recover, propagate, or crash loudly"
    ),
    "tautological-test": ("Assert on actual behavior; a test that cannot fail proves nothing"),
    "generic-name": (
        "Rename to describe the content: users, parsed_config, retry_count - not data/result"
    ),
    "meta-comment": ("Delete comments that narrate the task history; the diff tells that story"),
    "prose-block": ("Cut the block to the one non-obvious why; the code already shows the what"),
    "hardcoded-url": ("Move the environment-specific URL to configuration or the environment"),
    "hardcoded-id": (
        "Load the record identifier from configuration or the environment; "
        "source runs in every environment"
    ),
    "empty-function": (
        "Fill in the body or delete the declaration; an empty signature compiles and does nothing"
    ),
    "thin-wrapper": (
        "Inline the forwarding call; a wrapper must transform, log, guard "
        "or otherwise earn its layer"
    ),
    "unknown-directive": (
        "Delete the suppression comment or configure the tool it names; "
        "a directive for an absent tool suppresses nothing"
    ),
    "missing-tests": (
        "Add tests for the source files, or move generated vendored code "
        "behind an interface and ignore the directory"
    ),
    "unused-declaration": (
        "Delete it or wire it in; a definition nothing references is dead "
        "weight the next reader must still understand"
    ),
    "unused-dependency": (
        "Remove the dependency from the manifest or add the import that "
        "justifies it; declared weight slows every install"
    ),
}
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
