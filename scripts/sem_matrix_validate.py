"""Matrix expansion safety, reverse-engineered from Semaphore's
job_matrix_validator (roadmap C42, parity notes in
docs/semaphore-parity-research.md).

Semaphore fails a malformed matrix at compile time - duplicated
variable names, empty dimensions, a Cartesian product past the platform
ceiling. GitHub Actions expands silently and the bill arrives as a
hundred queued runners. This validator gives the repository the same
compile-time gate over `.github/workflows/*.yml`: duplicate mapping
keys (invisible to a plain `yaml.safe_load`, which silently keeps the
last), empty dimensions, dimensions that are not lists, a computed
product past the ceiling, `include`/`exclude` entries that reference
keys the matrix does not declare, and dynamic expansion
(`matrix: ${{ fromJSON(...) }}`) reported as unverifiable rather than
guessed at.

The ceiling is not Semaphore's 100 copied blindly: this repository's
runners are 2-core hosted machines behind the required gate, so the
shipped ceiling is 64 - inside one queue window and under GitHub's own
256-job ceiling. Documented here, override with ``--max``.

Exit codes: 0 valid, 1 violation found, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

MAX_MATRIX_JOBS = 64
_DYN_MARKERS = ("fromJSON", "${{")


class MatrixError(ValueError):
    """A workflow defect this validator reports, or a load failure."""


@dataclass
class MatrixViolation:
    """One compile-time matrix finding with its computed evidence."""

    workflow: str
    job: str
    kind: str  # duplicate-key|empty-dimension|non-list-dimension|product-over-ceiling|dangling-include|dangling-exclude|dynamic-expansion
    detail: str


def _unique_mapping(loader, node, deep: bool = False) -> dict:
    """construct_mapping that refuses duplicate keys at every level."""
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise MatrixError(f"duplicate mapping key {key!r}")
        seen.add(key)
    return loader.construct_mapping(node, deep)  # type: ignore[arg-type]


def _loader():
    """A SafeLoader subclass with duplicate-key refusal."""
    import yaml

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)
    return UniqueKeyLoader


def _iter_job_matrices(payload: dict) -> list[tuple[str, object]]:
    """(job name, strategy node) pairs for every job in one workflow."""
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict):
        return []
    out: list[tuple[str, object]] = []
    for name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        strategy = job.get("strategy")
        if isinstance(strategy, dict) and "matrix" in strategy:
            out.append((str(name), strategy["matrix"]))
    return out


def _is_dynamic(value: object) -> bool:
    """True for an expression value GitHub expands at run time."""
    return isinstance(value, str) and any(marker in value for marker in _DYN_MARKERS)


def _dimension_issue(key: object, value: object) -> MatrixViolation | None:
    """The empty-dimension or non-list-dimension finding, or None."""
    if value is None or (isinstance(value, (list, dict, str)) and not value):
        return MatrixViolation("", "", "empty-dimension", f"matrix key {key!r} is empty")
    if not isinstance(value, list):
        got = type(value).__name__
        return MatrixViolation(
            "", "", "non-list-dimension", f"matrix key {key!r} must be a list, got {got}"
        )
    return None


def _validate_dimensions(
    workflow: str,
    job: str,
    dims: dict,
    ceiling: int,
) -> list[MatrixViolation]:
    """Duplicate-free, non-empty, list-typed dimensions and the product.

    A dimension whose value is an expression (`${{ fromJSON(...) }}`) is
    GitHub-legal dynamic expansion: reported as unverifiable, excluded
    from the product math, and gate-neutral (visible, not failing).
    """
    violations: list[MatrixViolation] = []
    product = 1
    for key, value in dims.items():
        if _is_dynamic(value):
            violations.append(
                MatrixViolation(
                    workflow,
                    job,
                    "dynamic-expansion",
                    f"matrix key {key!r} expands dynamically ({str(value).strip()[:40]}...) - "
                    "unverifiable statically, gate-neutral",
                )
            )
            continue
        issue = _dimension_issue(key, value)
        if issue:
            violations.append(MatrixViolation(workflow, job, issue.kind, issue.detail))
            product = 0
            continue
        product *= max(len(value), 1)
    if product > ceiling:
        shape = " x ".join(str(len(v)) for v in dims.values() if isinstance(v, list) and v)
        violations.append(
            MatrixViolation(
                workflow,
                job,
                "product-over-ceiling",
                f"expansion {shape} = {product} jobs exceeds the ceiling {ceiling}",
            )
        )
    return violations


def _combinator_entry_issue(
    section: str,
    entry: object,
    dims: dict,
) -> MatrixViolation | None:
    """One include/exclude entry's finding, or None when acceptable.

    include may introduce keys (GitHub semantics); exclude may not.
    """
    if not isinstance(entry, dict):
        return MatrixViolation("", "", f"dangling-{section}", f"{section} entry must be a mapping")
    if section == "exclude":
        unknown = [k for k in entry if k not in dims]
        if unknown:
            return MatrixViolation(
                "", "", "dangling-exclude", f"exclude references undeclared keys {unknown}"
            )
    return None


def _validate_combinators(
    workflow: str,
    job: str,
    dims: dict,
    node: dict,
) -> list[MatrixViolation]:
    """include may introduce keys (GitHub semantics); exclude may not."""
    violations: list[MatrixViolation] = []
    for section in ("include", "exclude"):
        entries = node.get(section)
        if entries is None:
            continue
        if not isinstance(entries, list):
            violations.append(
                MatrixViolation(workflow, job, f"dangling-{section}", f"{section} must be a list")
            )
            continue
        for entry in entries:
            issue = _combinator_entry_issue(section, entry, dims)
            if issue:
                violations.append(MatrixViolation(workflow, job, issue.kind, issue.detail))
    return violations


def validate_one(
    workflow: str,
    job: str,
    node: object,
    ceiling: int,
) -> list[MatrixViolation]:
    """Every violation for one job's matrix node."""
    if isinstance(node, str):
        if any(marker in node for marker in _DYN_MARKERS):
            return [
                MatrixViolation(
                    workflow,
                    job,
                    "dynamic-expansion",
                    f"matrix expands dynamically ({node.strip()[:40]}...) - "
                    "cannot be validated statically, reported as unverifiable",
                )
            ]
        return []
    if not isinstance(node, dict):
        return []
    violations: list[MatrixViolation] = []
    dims = {k: v for k, v in node.items() if k not in ("include", "exclude")}
    violations.extend(_validate_dimensions(workflow, job, dims, ceiling))
    violations.extend(_validate_combinators(workflow, job, dims, node))
    return violations


def load_workflow(path: Path) -> dict:
    """One workflow file parsed with duplicate-key refusal."""
    import yaml

    try:
        payload = yaml.load(path.read_text(encoding="utf-8"), Loader=_loader())
    except MatrixError as exc:
        raise MatrixError(f"{path.name}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise MatrixError(f"{path.name}: YAML parse failure: {exc}") from exc
    if not isinstance(payload, dict):
        raise MatrixError(f"{path.name}: top level must be a mapping")
    return payload


def validate_root(root: Path, ceiling: int = MAX_MATRIX_JOBS) -> list[MatrixViolation]:
    """Every matrix violation in the repository's workflows."""
    wf_dir = root / ".github" / "workflows"
    violations: list[MatrixViolation] = []
    if not wf_dir.is_dir():
        return violations
    for path in sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml"))):
        try:
            payload = load_workflow(path)
        except MatrixError as exc:
            detail = str(exc)
            kind = "duplicate-key" if "duplicate mapping key" in detail else "workflow-parse"
            violations.append(MatrixViolation(path.name, "-", kind, detail))
            continue
        for job, node in _iter_job_matrices(payload):
            violations.extend(validate_one(path.name, job, node, ceiling))
    return violations


def main(argv: Sequence[str] | None = None, /) -> int:
    """CLI: validate every workflow matrix; JSON or human output.

    Exit 1 fires on hard violations only; ``dynamic-expansion`` rows are
    reported and gate-neutral, the same philosophy as analysis-coverage:
    unverifiable never masquerades as verified or as absent.
    """
    parser = argparse.ArgumentParser(description="validate workflow matrix expansion")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--max", type=int, default=MAX_MATRIX_JOBS, help="job ceiling")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"matrix-validate: root not found: {root}", file=sys.stderr)
        return 2
    violations = validate_root(root, args.max)
    hard = [v for v in violations if v.kind != "dynamic-expansion"]
    if args.json:
        print(
            json.dumps(
                {
                    "ceiling": args.max,
                    "violations": [vars(v) for v in violations],
                    "valid": not hard,
                },
                indent=2,
            )
        )
    else:
        print(f"matrix-validate: ceiling {args.max}, {len(violations)} report(s)")
        for v in violations:
            print(f"  {v.workflow} [{v.job}] {v.kind}: {v.detail}")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
