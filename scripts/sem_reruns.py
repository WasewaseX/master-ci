"""The rerun inventory: partial reruns and failed-job recovery (C43).

Semaphore exposes every rerun through its API inventory; GitHub Actions
has the same facts - each job's ``run_attempt`` in the runs/jobs JSON -
and this repository used none of them: a rerun was invisible unless a
human opened the run page and read attempt badges one by one. This
module makes the inventory mechanical:

- the input is the run-level Actions jobs JSON (what
    ``gh api repos/:repo/actions/runs/<id>/jobs`` returns), the same
    payload ``sem_ci_diagnose.py`` consumes, so one fetch feeds both
    tools;
- every job with ``run_attempt`` > 1 is a rerun: the inventory names
    it, its attempt count, its conclusion and whether the rerun
    recovered it (``success`` on an attempt above one) or it is still
    failing (a failed conclusion on the highest seen attempt);
- jobs without a ``run_attempt`` field are a recorded data gap - the
    inventory does not guess attempt one, it says the field is missing;
- totals, sorted rows, byte-identical runs for identical payloads: an
    inventory diff means the run changed, not the tool's mood.

Advisory by design - this reads what already happened and never gates:
the recovery question stays a human one, the inventory only makes it
visible. Malformed JSON is a configuration error (exit 2); a healthy
payload is exit 0 whether or not anything reran.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

_FAILED = frozenset({"failure", "timed_out", "cancelled", "startup_failure"})


def _job_row(job: dict) -> dict[str, object]:
    """One inventory row: identity, attempts, the recovery verdict."""
    attempt = job.get("run_attempt")
    conclusion = str(job.get("conclusion", "") or "unknown")
    name = str(job.get("name", "?"))
    if not isinstance(attempt, int):
        return {
            "job": name,
            "conclusion": conclusion,
            "gap": "run_attempt missing - the API did not report attempts",
        }
    reruns = attempt - 1
    if reruns <= 0:
        return {"job": name, "conclusion": conclusion, "reruns": 0}
    if conclusion == "success":
        state = "recovered"
    elif conclusion in _FAILED:
        state = "still-failing"
    else:
        state = "rerun-pending"
    return {
        "job": name,
        "conclusion": conclusion,
        "reruns": reruns,
        "state": state,
        "url": str(job.get("url", "")),
    }


def _rerun_count(row: dict[str, object]) -> int:
    """The row's rerun count; gap rows count zero."""
    value = row.get("reruns", 0)
    return int(value) if isinstance(value, int) else 0


def _parse_jobs(payload: str) -> list[dict[str, object]]:
    """The run-level jobs list, fail-closed on shape.

    Run-level payloads only: ``{"jobs": [...]}`` or a bare list. A
    dict that is neither is a misconfiguration - an inventory that
    guessed a single job out of a stray object would silently count
    one run as the whole pipeline.
    """
    data = json.loads(payload)
    if isinstance(data, dict):
        jobs = data.get("jobs")
    elif isinstance(data, list):
        jobs = data
    else:
        jobs = None
    if not isinstance(jobs, list):
        raise ValueError('jobs payload must be {"jobs": [...]} or a list of jobs')
    if any(not isinstance(job, dict) for job in jobs):
        raise ValueError("every job entry must be an object")
    return jobs


def _summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    """Totals over the rows, reruns sorted best-effort-first, gaps apart."""
    reruns = [row for row in rows if _rerun_count(row) and "gap" not in row]
    reruns.sort(key=lambda row: (-_rerun_count(row), str(row["job"])))
    gaps = [row for row in rows if "gap" in row]
    recovered = sum(1 for row in reruns if row.get("state") == "recovered")
    still_failing = sum(1 for row in reruns if row.get("state") == "still-failing")
    return {
        "schema": "rerun-inventory-v1",
        "jobs": len(rows),
        "rerun_jobs": len(reruns),
        "recovered": recovered,
        "still_failing": still_failing,
        "total_reruns": sum(_rerun_count(row) for row in reruns),
        "rows": reruns,
        "gaps": gaps,
    }


def build_inventory(payload: str) -> dict[str, object]:
    """The full inventory: rows, totals, gaps - deterministic order."""
    return _summarize([_job_row(job) for job in _parse_jobs(payload)])


def render_markdown(inventory: dict[str, object]) -> str:
    """The human report: totals first, every rerun named, gaps visible."""
    lines = [
        "# Rerun inventory",
        "",
        f"- jobs: {inventory['jobs']} - rerun jobs: {inventory['rerun_jobs']}"
        f" - total reruns: {inventory['total_reruns']}",
        f"- recovered: {inventory['recovered']} - still failing: {inventory['still_failing']}",
        "",
    ]
    rows: list[dict[str, object]] = inventory["rows"]  # type: ignore[assignment]
    for row in rows:
        suffix = f" - {row['state']}" if "state" in row else ""
        url = f" ({row['url']})" if row.get("url") else ""
        lines.append(
            f"- {row['job']}: attempt 1 + {row['reruns']} rerun(s)"
            f", conclusion {row['conclusion']}{suffix}{url}"
        )
    gaps: list[dict[str, object]] = inventory["gaps"]  # type: ignore[assignment]
    for gap in gaps:
        lines.append(f"- {gap['job']}: data gap - {gap['gap']}")
    if not rows and not gaps:
        lines.append("- no reruns, no gaps: every job ran once and stayed put")
    lines.append("")
    lines.append(
        "An inventory reads what already happened; it does not gate - the"
        " recovery question stays a human one."
    )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None, /) -> int:
    """CLI: --jobs-json PATH (or '-' for stdin) [--json] [--report PATH]."""
    parser = argparse.ArgumentParser(
        description="inventory partial reruns and failed-job recovery from Actions jobs JSON"
    )
    parser.add_argument("--jobs-json", required=True, help="Actions jobs JSON file, or - for stdin")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--report", default=None, help="also write the inventory JSON here")
    args = parser.parse_args(argv)
    source = args.jobs_json
    try:
        payload = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
        inventory = build_inventory(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"reruns: {exc}", file=sys.stderr)
        return 2
    if args.report:
        Path(args.report).write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(inventory, indent=2))
    else:
        sys.stdout.write(render_markdown(inventory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
