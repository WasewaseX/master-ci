"""SBOM generation and license policy judgment (roadmap A19).

The supply-chain story so far reads what is unused or vulnerable
(``slop_deps.py``, ``slop_ecosystem.py``, the ``vulnerable-dependency``
check) - it never answers the two questions an SBOM exists for: what
exactly does this tree declare as its third-party set, and which of
those declarations cross the license policy. The audit called both
docs-only. This module makes them mechanical; the per-ecosystem
reading rules live in ``sem_sbom_readers.py``, this module owns the
inventory, the policy and the report:

- the inventory: ``walk_manifests`` finds every supported manifest
    (``requirements*.txt`` as one filename pattern - a
    ``requirements-dev.txt`` is discovered exactly like
    ``requirements.txt`` - plus ``pyproject.toml``, ``package.json``,
    ``go.mod``, ``Cargo.toml``, ``Gemfile`` and ``*.csproj``), and
    ``parse_typed_manifest`` reads each one into package rows. Every
    dependency row carries ``NOASSERTION`` for the license - the
    manifest's license is the project's, not its dependencies'.
- the project license: the license a manifest declares is reported
    separately, per manifest - an MIT application does not make its
    unknown-license dependencies pass ``deny_unknown``, and the SPDX
    report never asserts MIT for a dependency nobody licensed.
- the policy: an allow/deny/``deny_unknown`` license policy judged per
    package; a package whose license is not declared is a visible
    ``NOASSERTION`` row, and under ``deny_unknown`` a violation - a
    missing license is a gap, and the policy decides whether gaps pass.
- the honesty rule: a manifest that cannot be parsed - or that parses
    into a shape the reader does not accept - is a recorded data gap
    with its reason, visible in the report, never a silent skip and
    never an exception that stops the scan. The SBOM is deterministic -
    identical trees render byte-identical documents (sorted rows, a
    content digest as the namespace, a constant creation timestamp the
    caller may override), so a diff of the SBOM shows the dependency
    change, not the mood.

Exit codes: 0 policy satisfied (or none given), 1 violations, 2
configuration error. Advisory by default - a gate that wants the
policy hard wires ``--policy`` into its own workflow.
"""

from __future__ import annotations

import argparse
import dataclasses as dc
import hashlib
import json
import sys
from pathlib import Path
from typing import Sequence

from sem_sbom_readers import (
    NOASSERTION,
    Package,
    parse_typed_manifest,
    walk_manifests,
)

SBOM_CREATED = "1970-01-01T00:00:00Z"


@dc.dataclass
class SbomResult:
    """The inventory, the project licenses, the verdicts and the gaps."""

    packages: list[Package] = dc.field(default_factory=list)
    project_licenses: list[dict[str, str]] = dc.field(default_factory=list)
    violations: list[dict[str, str]] = dc.field(default_factory=list)
    gaps: list[dict[str, str]] = dc.field(default_factory=list)

    def digest(self) -> str:
        payload = json.dumps([p.row() for p in self.packages], sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def scan_sbom(root: Path) -> SbomResult:
    """Inventory every manifest under root; unparseable ones are gaps."""
    result = SbomResult()
    for manifest in walk_manifests(root):
        rel = str(manifest.relative_to(root)) if root.is_dir() else manifest.name
        try:
            data = manifest.read_bytes()
        except OSError as exc:
            result.gaps.append({"manifest": rel, "reason": str(exc)[:160]})
            continue
        text = data.decode("utf-8", errors="replace")
        try:
            rows, declared = parse_typed_manifest(manifest.name, data, text, rel)
        except (OSError, ValueError) as exc:
            # The readers raise ValueError for every handled failure class:
            # wrong-shape documents, not-parseable XML, TOML/JSON decode
            # errors (all ValueError subclasses), so one bad manifest is
            # a recorded gap and the scan moves on.
            result.gaps.append({"manifest": rel, "reason": str(exc)[:160]})
            continue
        result.packages.extend(rows)
        if declared != NOASSERTION:
            result.project_licenses.append({"manifest": rel, "license": declared})
    return result


def load_policy(path: Path) -> dict[str, object]:
    """The license policy, fail-closed on schema and contradictions."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("policy must be a JSON object")
    for key in ("allow", "deny"):
        listed = data.get(key)
        if not isinstance(listed, list) or not all(
            isinstance(item, str) and item for item in listed
        ):
            raise ValueError(f"policy.{key} must be a list of license identifiers")
    overlap = {str(item) for item in data["allow"] if isinstance(item, str)} & {
        str(item) for item in data["deny"] if isinstance(item, str)
    }
    if overlap:
        raise ValueError(f"policy contradicts itself on {', '.join(sorted(overlap))}")
    if not isinstance(data.get("deny_unknown", False), bool):
        raise ValueError("policy.deny_unknown must be a boolean")
    return data


def _license_set(value: object) -> set[str]:
    """The policy's license list as a set, tolerating untyped payloads."""
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if isinstance(item, str)}


def judge_policy(packages: Sequence[Package], policy: dict[str, object]) -> list[dict[str, str]]:
    """Deny-list hits and (under deny_unknown) missing licenses."""
    allow = _license_set(policy.get("allow"))
    deny = _license_set(policy.get("deny"))
    deny_unknown = bool(policy.get("deny_unknown", False))
    violations: list[dict[str, str]] = []
    for package in packages:
        if package.license == NOASSERTION:
            if deny_unknown:
                violations.append(
                    {
                        "package": package.purl,
                        "license": NOASSERTION,
                        "kind": "unknown-license",
                        "manifest": package.manifest,
                    }
                )
            continue
        if package.license in deny and package.license not in allow:
            violations.append(
                {
                    "package": package.purl,
                    "license": package.license,
                    "kind": "denied-license",
                    "manifest": package.manifest,
                }
            )
    return sorted(violations, key=lambda row: row["package"])


def build_spdx(result: SbomResult, created: str = SBOM_CREATED) -> dict[str, object]:
    """The SPDX-2.3-shaped document, deterministic, digest-named."""
    packages = []
    for index, package in enumerate(sorted(result.packages, key=lambda p: (p.purl, p.manifest)), 1):
        packages.append(
            {
                "SPDXID": f"SPDXRef-Package-{index}",
                "name": package.name,
                "versionInfo": package.version,
                "licenseConcluded": package.license,
                "downloadLocation": NOASSERTION,
                "filesAnalyzed": False,
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": package.purl,
                    }
                ],
            }
        )
    digest = result.digest()
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"master-ci-sbom-{digest}",
        "documentNamespace": f"urn:master-ci:sbom:{digest}",
        "creationInfo": {"created": created, "creators": ["Tool: master-ci"]},
        "packages": packages,
    }


def render_markdown(result: SbomResult, policy: dict[str, object] | None) -> str:
    """The human report: inventory, project licenses, verdicts, gaps."""
    lines = [f"# SBOM and license report ({len(result.packages)} packages)", ""]
    for package in sorted(result.packages, key=lambda p: (p.purl, p.manifest)):
        lines.append(
            f"- {package.purl} - version: {package.version} - license: "
            f"{package.license} ({package.manifest})"
        )
    if result.project_licenses:
        lines += ["", "## Project licenses (the manifests' own, not their dependencies')", ""]
        for row in sorted(result.project_licenses, key=lambda row: row["manifest"]):
            lines.append(f"- {row['manifest']}: {row['license']}")
    if policy is not None:
        verdict = "violations" if result.violations else "satisfied"
        lines += ["", f"## License policy: {verdict}", ""]
        for row in result.violations:
            lines.append(f"- {row['kind']}: {row['package']} - {row['license']}")
    if result.gaps:
        lines += ["", "## Data gaps (manifests that could not be read)", ""]
        for gap in result.gaps:
            lines.append(f"- {gap['manifest']}: {gap['reason']}")
    lines.append("")
    return "\n".join(lines)


def main(
    argv: Sequence[str] | None = None,  # noqa: docs - the exploded form keeps the shared CLI signature off the boilerplate floor
) -> int:
    """CLI: --scan PATH [--policy PATH] [--sbom PATH] [--report PATH]."""
    parser = argparse.ArgumentParser(
        description="generate an SPDX-shaped SBOM and judge the license policy"
    )
    parser.add_argument("--scan", default=".", help="the tree to inventory")
    parser.add_argument("--policy", default=None, help="license policy JSON")
    parser.add_argument("--sbom", default=None, help="write the SPDX document here")
    parser.add_argument("--report", default=None, help="write the full report as JSON")
    args = parser.parse_args(argv)
    root = Path(args.scan)
    if not root.is_dir():
        print(f"sbom: scan root not found: {root}", file=sys.stderr)
        return 2
    policy: dict[str, object] | None = None
    if args.policy is not None:
        try:
            policy = load_policy(Path(args.policy))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"sbom: policy refused: {exc}", file=sys.stderr)
            return 2
    result = scan_sbom(root)
    result.violations = judge_policy(result.packages, policy) if policy else []
    if args.sbom:
        Path(args.sbom).write_text(
            json.dumps(build_spdx(result), indent=2) + "\n", encoding="utf-8"
        )
    if args.report:
        Path(args.report).write_text(
            json.dumps(
                {
                    "schema": "sbom-report-v1",
                    "packages": [
                        p.row() for p in sorted(result.packages, key=lambda p: (p.purl, p.manifest))
                    ],
                    "project_licenses": sorted(
                        result.project_licenses, key=lambda row: row["manifest"]
                    ),
                    "violations": result.violations,
                    "gaps": result.gaps,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(render_markdown(result, policy), end="")
    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
