"""Remedy hints, one per check id: the fix a finding's reader applies.

Split out of slop_model at v2.15 so the registry file stays inside the
same 400-line budget it enforces on consumers - the same split the CLI
made from the detector at v2.5. Finding.remedy keeps serving these;
keep reading REMEDIES off slop_model as before.
"""

from __future__ import annotations

REMEDIES = {
    "ai-vocab": "Rewrite the comment to describe intent in plain words",
    "emoji-comments": "Remove decorative emoji from code comments",
    "todo-density": "Resolve, ticket or delete the TODO/FIXME markers",
    "trivial-comments": "Delete comments that restate the code; explain why instead",
    "boilerplate-duplication": "Extract the repeated lines into a shared helper",
    "bloat-long-file": (
        "Split the file by responsibility; ask for the version doing the same job in fewer lines"
    ),
    "bloat-param-count": (
        "Group the parameters into an options object or struct; more than "
        "six parameters is unreadable at the call site"
    ),
    "high-cyclomatic": (
        "Split the function along its decision axes; 15+ resists review and safe change"
    ),
    "high-cognitive": (
        "Flatten the nesting and name the branches; cognitive 15+ is code a "
        "reviewer has to re-read to trust"
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
        "Verify the package and declare it; hallucinated names get squatted (slopsquatting)"
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
    "low-new-coverage": (
        "Test the lines this change adds before merging; uncovered new code "
        "is unverified code, and the floor keeps the gap from growing silently"
    ),
    "unused-declaration": (
        "Delete it or wire it in; a definition nothing references is dead "
        "weight the next reader must still understand"
    ),
    "orphan-module": (
        "Delete the module or give it a consumer; a file no import, config "
        "or script mentions is unreachable weight"
    ),
    "unused-dependency": (
        "Remove the dependency from the manifest or add the import that "
        "justifies it; declared weight slows every install"
    ),
    "import-cycle": (
        "Break the loop at the lowest-level module; move the shared "
        "symbols down so both sides depend on a leaf instead of each other"
    ),
    "low-cohesion": (
        "Split the class along the disconnected groups; each group of "
        "methods sharing one state cluster is a class of its own"
    ),
    "complexity-density": (
        "Flatten the decision ladders in the heaviest contributors; a "
        "project density over budget means complexity is concentrating"
    ),
    "duplication-density": (
        "Extract the repeated lines into shared helpers; the density is the trend the ratchet holds"
    ),
    "debt-ratio": "Pay down the worst check first; it is the pile the next release inherits",
    "high-coupling": "Split the importer by responsibility; each import is a change set it owns",
    "unstable-hub": "Freeze the hub's interface or push its churn down; dependents share it",
    # v2.15: the reliability dimension, the hotspot ranking and the
    # abstraction texture - six verdicts with their remedies.
    "unclosed-resource": (
        "Open the file in a with block or close it on every path; a held handle leaks"
    ),
    "shared-mutable-state": (
        "Annotate ClassVar when sharing is intentional, or build the container in __init__"
    ),
    "reliability-rating": "Pay the reliability defects first; the grade is the next incident's read",
    "hotspot-risk": "Split the hotspot's complexity and test it before the next change lands there",
    "forwarding-chain": (
        "Inline the ladder or make one hop transform; a pass-through chain nobody debugs"
    ),
    "no-behavior-class": (
        "Collapse the shell into the object it forwards to, or give it a behavior of its own"
    ),
    "java-broad-throws": (
        "Throw the specific exception type; a broad Exception hides every "
        "failure mode behind one signature"
    ),
    "eval-usage": (
        "Parse the input with a real parser or map the cases explicitly; "
        "eval compiles a string nobody reviewed"
    ),
    "ui-placeholder-image": (
        "Ship a real product photo, screenshot or face; placeholder "
        "services tell the reader nothing was chosen"
    ),
    "ui-identical-cards": (
        "Vary the layout between sections or extract one intentional "
        "component; six identical cards read as one template multiplied"
    ),
    "ui-em-dash-overuse": (
        "Use periods, commas, colons or hyphens; a pile of em dashes is a "
        "prose tell, not typography"
    ),
    "arch-forbidden-import": (
        "Import the module the architecture config approves; a forbidden "
        "import drags the wrong dependency direction into the codebase"
    ),
    "arch-layer-violation": (
        "Route the call through the layer boundary the config declares "
        "(an interface, an event, a service); layers leak by single imports"
    ),
    "arch-missing-pattern": (
        "Add the declared structure or narrow the rule's glob; a required "
        "pattern exists so readers can rely on it"
    ),
}
