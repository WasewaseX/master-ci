"""CI failure diagnosis from Actions job JSON and raw logs (roadmap B34).

When a hosted run goes red, the answer lives in job/step names and in
log lines, and today a human reads both by hand. This module makes the
first pass mechanical: given the Actions jobs JSON (what
``gh api repos/:repo/actions/runs/<id>/jobs`` returns) or a raw log
text, it names every failed step, classifies each failure against a
signature table the repository's own gates taught it, and attaches the
one remediation that class actually needs - all without guessing.

The honesty rule is the module's spine. A failure that matches no
signature is reported as ``unclassified`` with its evidence preserved,
never forced into the nearest bucket - and a line that reads clearly as
a failure (Actions error annotations, ``fatal:`` lines, ``Error:``
prefixes, a non-zero ``Process completed with exit code`` wrapper) keeps
its evidence as unclassified even when no signature names it, while
ordinary progress lines stay silence. ``confidence`` states how the
call was made: ``exact`` when the failed step's own name matched the
signature (the step name is authoritative), ``textual`` when only a
log line matched, ``learned`` when a reviewer-learnings store matched,
``unclassified`` when neither did. Actions log wrappers (the runner
timestamp and ``gh run view --log``'s job/step tabs) are stripped
before matching, because a signature matches the message, not the
wrapper. A diagnosis is advisory - the gate stays the gate, this only
reads what already happened.

Signature table: ruff check, ruff format, mypy, pytest, yamllint,
markdown-lint, the file-too-large hygiene ceiling, the quality ratchet
and the pin-lag watchdog - each mapping to the exact remediation
documented in docs/checks.md. The table grows with the gates; an
unknown tool is not a wrong answer, it is an honest one.
"""

from __future__ import annotations

import argparse
import dataclasses as dc
import json
import re
import sys
from pathlib import Path
from typing import Sequence

from sem_ci_learnings import Learning, load_learnings

_FAILED_CONCLUSIONS = frozenset({"failure", "timed_out"})
_NEUTRAL_CONCLUSIONS = frozenset({"success", "skipped", "neutral", "cancelled"})

# Lines that read as a failure even though no signature names them:
# preserved as unclassified evidence, while progress lines stay silence.
_GENERIC_FAILURES: tuple[re.Pattern[str], ...] = (
    re.compile(r"##\[error\]"),
    re.compile(r"\bfatal:\s"),
    re.compile(r"^Error:\s"),
    re.compile(r"Process completed with exit code (?!0\b)\d+"),
)

# The runner timestamp prefix on downloaded Actions log lines.
_RUNNER_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+")

# Linter kinds whose tool-name lines are not evidence by themselves.
_TOOL_NAME_KINDS = frozenset({"markdown-lint", "yamllint"})
_FAIL_WORD = re.compile(r"error|failed|warn", re.IGNORECASE)


@dc.dataclass
class Cause:
    """One classified failure: kind, how it was matched, the fix."""

    kind: str
    confidence: str  # 'exact' | 'textual' | 'unclassified'
    evidence: list[str] = dc.field(default_factory=list)
    remediation: str = ""


@dc.dataclass
class Diagnosis:
    """Every failed step or line, classified or honestly left open."""

    failures: list[Cause] = dc.field(default_factory=list)
    unclassified: int = 0

    @property
    def kinds(self) -> list[str]:
        return [c.kind for c in self.failures]


_SIGNATURES: tuple[tuple[str, tuple[re.Pattern[str], ...], str], ...] = (
    (
        "ruff-format",
        (re.compile(r"would reformat", re.IGNORECASE),),
        "run `ruff format .` locally and commit the reformatted files",
    ),
    (
        "ruff",
        (
            re.compile(r"(?:^|: ?)[EFW]\d{3} "),
            re.compile(r"ruff check failed", re.IGNORECASE),
        ),
        "run `ruff check .` locally, fix the named violations",
    ),
    (
        "mypy",
        (
            re.compile(
                r"error: (?:Cannot find|Incompatible|Missing|Function|Argument)", re.IGNORECASE
            ),
            re.compile(r"Found \d+ error", re.IGNORECASE),
        ),
        "run `mypy --ignore-missing-imports .` locally and fix the named errors",
    ),
    (
        "pytest",
        (re.compile(r"^(?:FAILED|ERROR) tests/"),),
        "run the failing test locally; a green suite never excuses a red one",
    ),
    (
        "file-too-large",
        (re.compile(r"file-too-large|file too large", re.IGNORECASE),),
        "split the file below the 400-line ceiling or document the suppression in docs/checks.md",
    ),
    (
        "quality-ratchet",
        (re.compile(r"quality ratchet|baseline (?:drift|regression)", re.IGNORECASE),),
        "fix the new findings or re-record the baseline deliberately; never lower the floor silently",
    ),
    (
        "pin-lag",
        (re.compile(r"pin lag|pin-lag", re.IGNORECASE),),
        "see the pin-lag watchdog section in docs/checks.md",
    ),
    (
        "yamllint",
        (re.compile(r"yaml\s*lint|lint\s*yaml", re.IGNORECASE),),
        "run `yamllint .` locally and fix the named lines",
    ),
    (
        "markdown-lint",
        (re.compile(r"markdown-lint|markdownlint", re.IGNORECASE),),
        "run the markdown linter locally and fix the named files",
    ),
)

_STEP_NAME_MATCHERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ruff-format", re.compile(r"ruff\s*format", re.IGNORECASE)),
    ("ruff", re.compile(r"ruff\s*(?:check|lint)", re.IGNORECASE)),
    ("mypy", re.compile(r"mypy", re.IGNORECASE)),
    ("pytest", re.compile(r"pytest|python tests|unit tests", re.IGNORECASE)),
    ("file-too-large", re.compile(r"file[- ]too[- ]large|hygiene", re.IGNORECASE)),
    ("yamllint", re.compile(r"yaml\s*lint|lint\s*yaml", re.IGNORECASE)),
    ("markdown-lint", re.compile(r"markdown", re.IGNORECASE)),
    ("quality-ratchet", re.compile(r"ratchet", re.IGNORECASE)),
    ("pin-lag", re.compile(r"pin\s*lag", re.IGNORECASE)),
)


def _classify_text(text: str) -> tuple[str, re.Pattern[str]] | None:
    """The first signature whose pattern matches the text."""
    for kind, patterns, _ in _SIGNATURES:
        for pattern in patterns:
            if pattern.search(text):
                return kind, pattern
    return None


def _classify_step(step_name: str) -> str | None:
    """The step name is authoritative: exact confidence on a match."""
    for kind, matcher in _STEP_NAME_MATCHERS:
        if matcher.search(step_name):
            return kind
    return None


def _remediation(kind: str) -> str:
    for entry_kind, _, remediation in _SIGNATURES:
        if entry_kind == kind:
            return remediation
    return ""


def _strip_actions_prefix(line: str) -> str:
    """Strip the Actions log wrapper: gh's job/step tabs, then the timestamp.

    Downloaded Actions logs read ``2026-11-20T10:00:00Z FAILED tests/...``
    and ``gh run view --log`` prepends ``job<TAB>step<TAB>`` before the
    timestamp; signatures match the message, not the wrapper. The tab
    segment is taken only when its tail carries a runner timestamp, so a
    tab inside real output cannot swallow the message.
    """
    core = line
    if "\t" in core:
        tail = core.rsplit("\t", 1)[1]
        if _RUNNER_TIMESTAMP.match(tail):
            core = tail
    return _RUNNER_TIMESTAMP.sub("", core, count=1).strip()


def _looks_like_failure(core: str) -> bool:
    """True for lines that clearly failed without naming a known tool."""
    return any(pattern.search(core) for pattern in _GENERIC_FAILURES)


def _match_learning(learnings: tuple[Learning, ...], core: str) -> Learning | None:
    """The first learning whose compiled pattern matches the line."""
    for learning in learnings:
        if learning.compiled.search(core):
            return learning
    return None


def _append_learned(diagnosis: "Diagnosis", kind: str, remediation: str, evidence: str) -> None:
    """Group a learned signature's hits, carrying the learning's remediation."""
    for cause in diagnosis.failures:
        if cause.kind == kind and cause.confidence == "learned":
            cause.evidence.append(evidence)
            return
    diagnosis.failures.append(
        Cause(kind=kind, confidence="learned", evidence=[evidence], remediation=remediation)
    )


def _line_hit(core: str, learnings: tuple[Learning, ...]) -> tuple[str, str, str] | None:
    """(kind, confidence, remediation) for one log line; None is silence.

    Signatures match first, the reviewer-learnings store second, and a
    line that clearly reads as a failure without either becomes
    unclassified evidence; ordinary progress lines are silence. A
    linter-tool-name line is only evidence when it also reads as a
    failure - the tool naming itself is not a failure of the tool.
    """
    hit = _classify_text(core)
    if hit is not None:
        kind = hit[0]
        if kind in _TOOL_NAME_KINDS and not _FAIL_WORD.search(core):
            return None
        return kind, "textual", _remediation(kind)
    learning = _match_learning(learnings, core)
    if learning is not None:
        return learning.kind, "learned", learning.remediation
    if _looks_like_failure(core):
        return "unclassified", "unclassified", ""
    return None


def diagnose_log(text: str, learnings: tuple[Learning, ...] = ()) -> "Diagnosis":
    """Classify a raw log: every matching line becomes evidence.

    Each line gets one verdict from ``_line_hit``; unclassified
    evidence is grouped into one cause and counted - never dropped -
    while ordinary progress lines stay silence.
    """
    diagnosis = Diagnosis()
    stray: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        evidence = f"line {lineno}: {stripped[:200]}"
        hit = _line_hit(_strip_actions_prefix(stripped), learnings)
        if hit is None:
            continue
        kind, confidence, remediation = hit
        if kind == "unclassified":
            stray.append(evidence)
        elif confidence == "learned":
            _append_learned(diagnosis, kind, remediation, evidence)
        else:
            _append(diagnosis, kind, confidence, evidence)
    if stray:
        diagnosis.failures.append(
            Cause(kind="unclassified", confidence="unclassified", evidence=stray)
        )
        diagnosis.unclassified += len(stray)
    return diagnosis


def _failed_steps(job: dict) -> list[str]:
    """Names of the job's own failed steps; the rest did not fail."""
    return [
        str(step.get("name", ""))
        for step in job.get("steps", [])
        if isinstance(step, dict) and str(step.get("conclusion", "")) in _FAILED_CONCLUSIONS
    ]


def _job_causes(job: dict, diagnosis: "Diagnosis", learnings: tuple[Learning, ...] = ()) -> bool:
    """Classify one failed job into diagnosis; True when anything matched."""
    matched_any = False
    for name in _failed_steps(job):
        kind = _classify_step(name)
        _append(diagnosis, kind, "exact" if kind else None, f"step '{name}'")
        matched_any = matched_any or kind is not None
    excerpt = str(job.get("log_excerpt", "") or "")
    if excerpt.strip():
        for cause in diagnose_log(excerpt, learnings).failures:
            _append_existing(diagnosis, cause)
            matched_any = True
    if not matched_any:
        _append(
            diagnosis,
            None,
            None,
            f"job '{job.get('name', '?')}' failed with no signature match",
        )
    return matched_any


def diagnose_jobs(payload: str, learnings: tuple[Learning, ...] = ()) -> "Diagnosis":
    """Classify the Actions jobs JSON: failed steps first, log text second.

    The payload is the API's ``{"jobs": [...]}`` (or a single job
    object). Steps with a failed conclusion are matched by step name
    (``exact``); jobs whose step names explain nothing fall back to
    their log excerpt's text when the API supplied one. Conclusions
    that mean "did not run" are never failures, and a job with no
    failing step yields no cause - skipped work is claimed as nothing.
    """
    diagnosis = Diagnosis()
    data = json.loads(payload)
    jobs = data.get("jobs", [data]) if isinstance(data, dict) else data
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if str(job.get("conclusion", "")) not in _FAILED_CONCLUSIONS:
            continue
        _job_causes(job, diagnosis, learnings)
    return diagnosis


def _append(diagnosis: Diagnosis, kind: str | None, confidence: str | None, evidence: str) -> None:
    """Add or extend one cause; None kind becomes an unclassified row."""
    if kind is None or confidence is None:
        diagnosis.unclassified += 1
        diagnosis.failures.append(
            Cause(kind="unclassified", confidence="unclassified", evidence=[evidence])
        )
        return
    for cause in diagnosis.failures:
        if cause.kind == kind and cause.confidence == confidence:
            cause.evidence.append(evidence)
            return
    diagnosis.failures.append(
        Cause(kind=kind, confidence=confidence, evidence=[evidence], remediation=_remediation(kind))
    )


def _append_existing(diagnosis: Diagnosis, cause: Cause) -> None:
    """Merge a sub-diagnosis's causes, keeping remediations attached."""
    for existing in diagnosis.failures:
        if existing.kind == cause.kind and existing.confidence == cause.confidence:
            existing.evidence.extend(cause.evidence)
            return
    diagnosis.failures.append(cause)
    if cause.kind == "unclassified":
        diagnosis.unclassified += 1


def render_markdown(diagnosis: Diagnosis) -> str:
    """The report: causes with evidence, unclassified left unclassified."""
    lines = ["# CI failure diagnosis", ""]
    if not diagnosis.failures:
        lines += ["No failure matched the signature table - and nothing failed to hide."]
        return "\n".join(lines) + "\n"
    for cause in diagnosis.failures:
        title = f"**{cause.kind}** ({cause.confidence})"
        lines.append(f"- {title}")
        for evidence in cause.evidence[:5]:
            lines.append(f"  - {evidence}")
        if len(cause.evidence) > 5:
            lines.append(f"  - ... {len(cause.evidence) - 5} more evidence rows")
        if cause.remediation:
            lines.append(f"  - remediation: {cause.remediation}")
    lines += [
        "",
        "A diagnosis reads what already happened; it does not replace the gate,",
        "and an unclassified failure is reported as unknown, never guessed.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, /) -> int:
    """CLI: --jobs-json (Actions jobs JSON) or --log (raw text), '-' stdin.

    ``--learnings`` names a reviewer-learnings store applied after the
    signature table; a store that cannot be loaded refuses the run.
    """
    parser = argparse.ArgumentParser(description="diagnose a failed CI run from jobs JSON or logs")
    parser.add_argument("--jobs-json", default=None, help="Actions jobs JSON file, or - for stdin")
    parser.add_argument("--log", default=None, help="raw log text file, or - for stdin")
    parser.add_argument("--learnings", default=None, help="reviewer-learnings JSON store to apply")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    if bool(args.jobs_json) == bool(args.log):
        parser.error("exactly one of --jobs-json or --log is required")
    learnings: tuple[Learning, ...] = ()
    if args.learnings is not None:
        try:
            learnings = load_learnings(Path(args.learnings))
        except (ValueError, OSError) as exc:
            print(f"ci-diagnose: learnings store refused: {exc}", file=sys.stderr)
            return 2
    source = args.jobs_json or args.log
    if source == "-":
        text = sys.stdin.read()
    elif Path(source).is_file():
        text = Path(source).read_text(encoding="utf-8")
    else:
        print(f"ci-diagnose: input file not found: {source}", file=sys.stderr)
        return 2
    try:
        if args.jobs_json is not None:
            diagnosis = diagnose_jobs(text, learnings)
        else:
            diagnosis = diagnose_log(text, learnings)
    except json.JSONDecodeError as exc:
        print(f"ci-diagnose: malformed jobs JSON: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "kinds": diagnosis.kinds,
                    "unclassified": diagnosis.unclassified,
                    "failures": [
                        {
                            "kind": c.kind,
                            "confidence": c.confidence,
                            "evidence": c.evidence,
                            "remediation": c.remediation,
                        }
                        for c in diagnosis.failures
                    ],
                },
                indent=2,
            )
        )
    else:
        print(render_markdown(diagnosis), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
