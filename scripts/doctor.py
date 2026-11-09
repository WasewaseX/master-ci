#!/usr/bin/env python3
"""Doctor: independent verification of a consumer's master-ci setup.

The template verifies code; doctor verifies the *setup* - the workflow
file, the pins and the permissions a consumer wrote by hand. It runs
offline against the consumer's own checkout (or any directory passed via
``--workflows-dir``) and answers, deterministically, the questions the
README answers in prose:

- is the reusable workflow ref pinned in a reproducible way?
- does a foreign caller carry the explicit helper pin that meta's
fail-closed lookup expects?
- are the permissions the jobs actually need declared where the job
can see them?
- is the aggregate ``ci-success`` check the one to mark required?

Findings have three severities: ``error`` (a contract violation - the
run is degraded or fails today), ``warning`` (works, but reproducibility
or reporting is reduced) and ``info`` (an optional capability that is
switched off). Exit codes: 0 healthy, warnings allowed; 1 at least one
error; 2 doctor could not diagnose at all (missing PyYAML, no workflow
directory, a workflow file it cannot parse). Fail closed, like the rest
of the template: an unreadable setup is an error, never a shrug.

YAML parsing needs PyYAML; without it doctor refuses to guess:
``pip install pyyaml`` and run again.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TEMPLATE = "WasewaseX/master-ci"
KNOWN_REUSABLE = {
    "master-ci.yml",
    "codeql.yml",
    "deploy-aws.yml",
    "release-docker.yml",
}
TAG_REF = re.compile(r"^v\d+\.\d+\.\d+$")
USES_RE = re.compile(r"^(?P<slug>[^@]+?)/\.github/workflows/(?P<workflow>[\w.-]+)@(?P<ref>\S+)$")
PERMISSION_NEEDS = {
    "contents": ("error", "checkout and the dependency review need contents: read"),
    "security-events": (
        "error",
        "SARIF uploads to the Security tab need security-events: write",
    ),
    "pull-requests": (
        "warning",
        "inline PR annotations and the quality report degrade without pull-requests: write",
    ),
    "id-token": (
        "info",
        "OIDC capabilities (Scorecard publishing, AWS deploy) need id-token: write",
    ),
}


@dataclass(frozen=True)
class Note:
    """One diagnostic: severity, stable code, human message."""

    severity: str  # error | warning | info
    code: str
    message: str


def _load_yaml(path, pyyaml):
    try:
        return pyyaml.safe_load(path.read_text(encoding="utf-8")), None
    except Exception as exc:  # yaml.YAMLError and friends - fail closed
        return None, f"cannot parse {path.name}: {exc}"


def _job_permissions(doc: dict, job_name: str) -> dict | None:
    """Effective permissions for one job: job-level wins over workflow-level."""
    job = (doc.get("jobs") or {}).get(job_name) or {}
    perms = job.get("permissions")
    if perms is None:
        perms = doc.get("permissions")
    return perms if isinstance(perms, dict) else perms


def _ref_style_notes(ref: str, codes) -> list[Note]:
    """Ref style for one `uses:` ref: mutable branches error, others warn."""
    if ref == "main" or ref in {"master", "develop"}:
        return [
            Note(
                "error",
                codes["mutable"],
                f"@{ref} is a mutable ref - CI behavior changes without review; pin a release tag",
            )
        ]
    if not TAG_REF.match(ref):
        return [
            Note(
                "warning",
                codes["floating"],
                f"@{ref} is not a release tag (vX.Y.Z) - branches are mutable and SHAs do not track releases",
            )
        ]
    return []


def _helper_pin_notes(template_ref, template_repo, codes) -> list[Note]:
    """The explicit helper-pin contract - a master-ci.yml input pair only."""
    if template_repo and not template_ref:
        return [
            Note(
                "error",
                codes["missing-helper-ref"],
                "template_repo is set without template_ref - a foreign helper repo needs an explicit pin (meta fails closed otherwise)",
            )
        ]
    if not template_repo and not template_ref:
        return [
            Note(
                "warning",
                codes["implicit-helper"],
                "no template_repo/template_ref - helpers resolve to the calling repo; foreign projects must pin both",
            )
        ]
    if template_repo and template_ref:
        return [Note("info", codes["pinned"], f"helpers pinned to {template_repo}@{template_ref}")]
    return []


def _permission_notes(perms, codes) -> list[Note]:
    if perms is None:
        return [
            Note(
                "error",
                codes["perms-missing"],
                "no permissions block - declare least-privilege job permissions explicitly",
            )
        ]
    notes: list[Note] = []
    needed = {"contents": "read", "security-events": "write", "pull-requests": "write"}
    for key, grant in needed.items():
        got = perms.get(key)
        if got == grant:
            continue
        if key == "contents" and got not in (None, "read"):
            notes.append(
                Note(
                    "error",
                    codes["perms"],
                    f"contents should be 'read' (least privilege), got {got!r}",
                )
            )
            continue
        severity, why = PERMISSION_NEEDS[key]
        notes.append(Note(severity, codes["perms"], f"{key}: {grant} not granted - {why}"))
    if perms.get("id-token") != "write":
        severity, why = PERMISSION_NEEDS["id-token"]
        notes.append(Note(severity, codes["perms"], f"id-token: write not granted - {why}"))
    return notes


def _inputs_of(job: dict) -> dict:
    with_block = job.get("with")
    return with_block if isinstance(with_block, dict) else {}


def check_master_job(doc: dict, job_name: str, ref: str, template: str, codes) -> list[Note]:
    """The deep contract checks for one master-ci reusable-workflow job."""
    job = (doc.get("jobs") or {}).get(job_name) or {}
    notes = _ref_style_notes(ref, codes)
    notes.extend(
        _helper_pin_notes(
            _inputs_of(job).get("template_ref"), _inputs_of(job).get("template_repo"), codes
        )
    )
    notes.extend(_permission_notes(_job_permissions(doc, job_name), codes))
    coverage_min = _inputs_of(job).get("coverage_min")
    if coverage_min is not None:
        if (
            not isinstance(coverage_min, int)
            or isinstance(coverage_min, bool)
            or not 0 <= coverage_min <= 100
        ):
            notes.append(
                Note(
                    "error",
                    codes["coverage"],
                    f"coverage_min must be an integer 0-100, got {coverage_min!r}",
                )
            )
        elif coverage_min > 0:
            notes.append(Note("info", codes["coverage"], f"coverage floor armed at {coverage_min}"))
    if _inputs_of(job).get("diff_base"):
        notes.append(
            Note(
                "info",
                codes["new-code"],
                "diff_base set - the new-code gate is armed for added lines",
            )
        )
    return notes


def _aggregate_notes(raw_texts: list[str], codes) -> list[Note]:
    """Repo-wide checks: the required-check reminder (raw text, comments count)."""
    notes: list[Note] = []
    blob = "\n".join(raw_texts)
    if "ci-success" not in blob:
        notes.append(
            Note(
                "info",
                codes["required-check"],
                "no workflow mentions ci-success - remember to mark it as the required status check",
            )
        )
    return notes


NOTE_CODES = (
    "mutable",
    "floating",
    "missing-helper-ref",
    "implicit-helper",
    "pinned",
    "perms",
    "perms-missing",
    "coverage",
    "new-code",
    "required-check",
    "codeql-languages",
)


def _codes() -> dict:
    return {k: f"doctor-{k}" for k in NOTE_CODES}


def _load_all(workflows_dir, pyyaml) -> tuple[list[dict], list[str]] | Note:
    """Parse every workflow file; a Note means doctor cannot diagnose."""
    if not workflows_dir.is_dir():
        return Note("error", "doctor-internal", f"no workflows directory at {workflows_dir}")
    names = sorted(p for p in workflows_dir.iterdir() if p.suffix in (".yml", ".yaml"))
    if not names:
        return Note("error", "doctor-internal", f"no workflow files in {workflows_dir}")
    docs: list[dict] = []
    raw_texts: list[str] = []
    for path in names:
        raw_texts.append(path.read_text(encoding="utf-8", errors="replace"))
        doc, err = _load_yaml(path, pyyaml)
        if err:
            return Note("error", "doctor-internal", err)
        if isinstance(doc, dict):
            docs.append(doc)
    return docs, raw_texts


def _notes_for_job(
    doc: dict, job_name: str, workflow: str, ref: str, template: str, codes: dict
) -> list[Note]:
    """Notes for one `uses:` that points at the template."""
    if workflow == "master-ci.yml":
        return check_master_job(doc, job_name, ref, template, codes)
    if workflow in KNOWN_REUSABLE:
        return _ref_style_notes(ref, codes) + _permission_notes(
            _job_permissions(doc, job_name), codes
        )
    return []


def _scan_doc(doc: dict, template: str, codes: dict) -> list[Note]:
    notes: list[Note] = []
    for job_name, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict) or not isinstance(job.get("uses"), str):
            continue
        match = USES_RE.match(job["uses"])
        if not match or match.group("slug") != template:
            continue
        notes.extend(
            _notes_for_job(
                doc, job_name, match.group("workflow"), match.group("ref"), template, codes
            )
        )
    return notes


def diagnose(workflows_dir, template: str = DEFAULT_TEMPLATE) -> list[Note]:
    """Run every check; return the notes in stable order."""
    try:
        import yaml as pyyaml
    except ImportError:
        return [Note("error", "doctor-internal", "PyYAML is required: pip install pyyaml")]
    prepared = _load_all(workflows_dir, pyyaml)
    if isinstance(prepared, Note):
        return [prepared]
    docs, raw_texts = prepared
    codes = _codes()
    notes: list[Note] = []
    for doc in docs:
        notes.extend(_scan_doc(doc, template, codes))
    if not notes:
        notes.append(
            Note(
                "info",
                codes["required-check"],
                f"no job uses {template} workflows yet - see examples/ci.yml",
            )
        )
    notes.extend(_aggregate_notes(raw_texts, codes))
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(notes, key=lambda n: (order[n.severity], n.code, n.message))


def _print_human(notes: list[Note]) -> None:
    for note in notes:
        print(f"[{note.severity}] {note.code}: {note.message}")
    bad = sum(n.severity == "error" for n in notes)
    print(f"doctor: {len(notes)} finding(s), {bad} error(s)")


def _print_json(notes: list[Note]) -> None:
    print(
        json.dumps(
            {
                "ok": not any(n.severity == "error" for n in notes),
                "errors": [n.message for n in notes if n.severity == "error"],
                "warnings": [n.message for n in notes if n.severity == "warning"],
                "infos": [n.message for n in notes if n.severity == "info"],
            },
            indent=2,
        )
    )


def _exit_code(notes: list[Note]) -> int:
    if any(n.code == "doctor-internal" for n in notes):
        return 2
    if any(n.severity == "error" for n in notes):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a master-ci consumer setup (offline)")
    parser.add_argument(
        "--workflows-dir", default=".github/workflows", help="workflow directory to inspect"
    )
    parser.add_argument(
        "--template", default=DEFAULT_TEMPLATE, help="template slug your workflows call into"
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="machine-readable output"
    )
    args = parser.parse_args(argv)
    notes = diagnose(Path(args.workflows_dir), template=args.template)
    if args.as_json:
        _print_json(notes)
    else:
        _print_human(notes)
    return _exit_code(notes)


if __name__ == "__main__":
    sys.exit(main())
