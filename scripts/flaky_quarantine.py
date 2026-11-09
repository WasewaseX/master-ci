#!/usr/bin/env python3
"""The quarantine loop: turn a red hunt into a reviewable quarantine PR.

The flake hunt (``flaky_detect.py``) measures and gates; this module acts
on its verdict, the same PR-only way the fix engine acts on audit
findings. Two modes:

- **propose** - read the hunt report, merge the new flaky tests into a
  candidate baseline (append-only: existing entries keep their
  ``first_seen`` verbatim, nothing is ever removed - removing a healed
  flake is a reviewed commit), and write the candidate plus a PR body
  next to it. A repo in report-only mode (no committed baseline) is
  refused without artifacts: adopting a first floor is a human commit,
  not an automation side effect.
- **verify** - the quarantine job's independent second opinion before a
  PR can open: the candidate must be exactly the committed baseline
  unioned with the report's new flaky set. No removal, no fabrication,
  ``first_seen`` preserved, new entries dated. Any mismatch fails
  closed (exit 2).

Exit codes: 0 (nothing to do, proposal written, or verification
passed), 2 (fail-closed: unreadable report or baseline, inconsistent
evidence, attempted removal or rewrite). A quarantine built on doubt is
never opened.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flaky_detect import BASELINE_VERSION, HuntError, load_baseline
from flaky_parsers import FAILED, PASSED

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BASELINE_NAME = "baseline.json"
BODY_NAME = "body.md"


def read_report(path: Path) -> dict:
    """Read the hunt report; a proposal needs evidence, not hope."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HuntError(f"report {path} does not exist - the hunt produced no verdict") from None
    except json.JSONDecodeError as exc:
        raise HuntError(f"report {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != BASELINE_VERSION:
        raise HuntError(f"report {path} must be version {BASELINE_VERSION}")
    new = payload.get("new_flaky")
    if not isinstance(new, list) or any(not isinstance(key, str) for key in new):
        raise HuntError(f"report {path}: 'new_flaky' must be a list of test keys")
    if not isinstance(payload.get("flaky"), list):
        raise HuntError(f"report {path}: 'flaky' must be a list")
    return payload


def check_evidence(report: dict) -> tuple[list[str], dict[str, list[str]]]:
    """Every new key must be backed by pass+fail outcomes in the report.

    The report is the only evidence the quarantine rests on; if its own
    flaky list does not show the coin flip, the report is inconsistent
    and the proposal fails closed instead of trusting a summary field.
    """
    outcomes = {entry["key"]: entry.get("outcomes", []) for entry in report["flaky"]}
    new = list(report["new_flaky"])
    for key in new:
        runs = outcomes.get(key)
        if runs is None or PASSED not in runs or FAILED not in runs:
            raise HuntError(
                f"report claims new flaky test {key!r} but its flaky list does "
                "not show both a pass and a fail - refusing to quarantine "
                "from inconsistent evidence"
            )
    return new, outcomes


def merge_baseline(existing: dict[str, dict], new: list[str], today: str) -> dict[str, dict]:
    """Append-only union: originals verbatim, new keys stamped today."""
    merged = dict(existing)
    for key in new:
        if key in merged:
            raise HuntError(f"{key} is already quarantined - not a new flake")
        merged[key] = {"first_seen": today}
    if not set(existing) <= set(merged) or set(merged) != set(existing) | set(new):
        raise HuntError("merge invariant broken - refusing to write the candidate")
    return merged


def build_body(new: list[str], outcomes: dict[str, list[str]], run_url: str) -> str:
    """The PR body: the evidence table, the close-rejects rule, the rerun."""
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        f"The scheduled flake hunt found {len(new)} new flaky test(s) on the",
        "default branch - test(s) whose verdict changed between iterations.",
        "This PR appends them to `.flaky-baseline.json`, the quarantine list.",
        "Everything already in the baseline is preserved verbatim; nothing is",
        "ever removed by automation - removing a healed flake is a reviewed",
        "commit.",
        "",
        "| Test | Outcomes | First seen |",
        "|---|---|---|",
    ]
    lines += [f"| `{key}` | {' -> '.join(outcomes.get(key, []))} | {today} |" for key in new]
    lines += [
        "",
        "Closing this PR rejects the quarantine: the tests stay unquarantined",
        "and the next hunt keeps failing until the flakes are fixed. Merging",
        "it silences only the listed tests - a new flake still fails the",
        "next hunt.",
        "",
        "Reproduce locally:",
        "",
        "```",
        "python3 _ci/scripts/flaky_detect.py --results-dir flaky-results \\",
        "  --baseline .flaky-baseline.json --fail-on-flaky",
        "```",
    ]
    if run_url:
        lines += ["", f"Evidence for this run: {run_url}"]
    return "\n".join(lines) + "\n"


def propose(report_path: Path, baseline_path: Path, out_dir: Path, run_url: str) -> int:
    """Build the candidate baseline and PR body; refuse anything doubtful."""
    report = read_report(report_path)
    new, outcomes = check_evidence(report)
    if not new:
        print("flaky quarantine: no new flaky test(s) - nothing to propose")
        return 0
    if not baseline_path.exists():
        print(
            "flaky quarantine: no committed baseline - report-only mode; "
            "adopting a first floor is a human commit, not an automation "
            "side effect"
        )
        return 0
    try:
        existing = load_baseline(baseline_path)
    except HuntError as exc:
        raise HuntError(f"{exc} - refusing to build a quarantine on an unreadable floor") from None
    today = datetime.now(timezone.utc).date().isoformat()
    merged = merge_baseline(existing, new, today)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"version": BASELINE_VERSION, "tests": merged}
    (out_dir / BASELINE_NAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / BODY_NAME).write_text(build_body(new, outcomes, run_url), encoding="utf-8")
    print(
        f"flaky quarantine: proposing {len(new)} new flaky test(s); "
        f"{len(existing)} existing entr(y|ies) preserved verbatim"
    )
    return 0


def verify(merged_path: Path, baseline_path: Path, report_path: Path) -> int:
    """The second opinion: candidate must be exactly baseline union new."""
    report = read_report(report_path)
    new, _ = check_evidence(report)
    try:
        existing = load_baseline(baseline_path)
        candidate = load_baseline(merged_path)
    except HuntError as exc:
        raise HuntError(f"{exc} - verification fails closed") from None
    expected = set(existing) | set(new)
    if set(candidate) != expected:
        missing = sorted(expected - set(candidate))
        extra = sorted(set(candidate) - expected)
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"unexpected {extra}")
        raise HuntError(
            "; ".join(detail) + " - removal is a reviewed commit, never a PR side effect"
        )
    for key, entry in existing.items():
        if candidate[key]["first_seen"] != entry["first_seen"]:
            raise HuntError(f"{key}: first_seen was rewritten - the quarantine is append-only")
    for key in new:
        if not ISO_DATE.match(candidate[key]["first_seen"]):
            raise HuntError(
                f"{key}: first_seen {candidate[key]['first_seen']!r} is not an ISO date"
            )
    print(f"flaky quarantine: verified - {len(existing)} preserved + {len(new)} new")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="flake-hunt quarantine loop")
    sub = parser.add_subparsers(dest="command", required=True)
    do_propose = sub.add_parser("propose", help="merge new flakes into a candidate baseline")
    do_propose.add_argument("--report", required=True, type=Path, help="hunt report JSON")
    do_propose.add_argument("--baseline", required=True, type=Path, help="committed baseline")
    do_propose.add_argument("--out", required=True, type=Path, help="output directory")
    do_propose.add_argument("--run-url", default="", help="hunt run URL for the PR body")
    do_verify = sub.add_parser("verify", help="independently re-check the candidate")
    do_verify.add_argument("--merged", required=True, type=Path, help="candidate baseline")
    do_verify.add_argument("--baseline", required=True, type=Path, help="committed baseline")
    do_verify.add_argument("--report", required=True, type=Path, help="hunt report JSON")
    args = parser.parse_args(argv)
    try:
        if args.command == "propose":
            return propose(args.report, args.baseline, args.out, args.run_url)
        return verify(args.merged, args.baseline, args.report)
    except HuntError as exc:
        print(f"flaky quarantine: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
