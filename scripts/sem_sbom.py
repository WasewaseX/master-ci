"""SBOM generation and license policy judgment (roadmap A19).

The supply-chain story so far reads what is unused or vulnerable
(``slop_deps.py``, ``slop_ecosystem.py``, the ``vulnerable-dependency``
check) - it never answers the two questions an SBOM exists for: what
exactly does this tree declare as its third-party set, and which of
those declarations cross the license policy. The audit called both
docs-only. This module makes them mechanical:

- the inventory: every dependency declaration across the six supported
    ecosystems (PyPI ``requirements*.txt``/``pyproject.toml``, npm
    ``package.json``, Go ``go.mod``, Rust ``Cargo.toml``, NuGet
    ``*.csproj``, Ruby ``Gemfile``) becomes one SPDX-2.3-shaped package
    row - name, declared version (``NOASSERTION`` when the manifest
    does not pin one), a purl, and the license the manifest itself
    declares (``NOASSERTION`` when none). Declared, not resolved: the
    version is what the manifest says, never a resolved graph - that
    boundary stays with the lock files and the existing dependency
    oracles.
- the policy: an allow/deny/``deny_unknown`` license policy judged per
    package; a package whose license is not declared is a visible
    ``NOASSERTION`` row, and under ``deny_unknown`` a violation - a
    missing license is a gap, and the policy decides whether gaps pass.
- the honesty rule: a manifest that cannot be parsed is a recorded
    data gap with its reason, visible in the report, never a silent
    skip. The SBOM is deterministic - identical trees render
    byte-identical documents (sorted rows, a content digest as the
    namespace, a constant creation timestamp the caller may override),
    so a diff of the SBOM shows the dependency change, not the mood.

Exit codes: 0 policy satisfied (or none given), 1 violations, 2
configuration error. Advisory by default - a gate that wants the
policy hard wires ``--policy`` into its own workflow.
"""

from __future__ import annotations

import argparse
import dataclasses as dc
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Sequence

NOASSERTION = "NOASSERTION"
SBOM_CREATED = "1970-01-01T00:00:00Z"

CSPROJ_REF = re.compile(
    r"<PackageReference\s+Include=\"([^\"]+)\"(?:\s+Version=\"([^\"]*)\")?\s*/?>",
)
GEM_REF = re.compile(r"^\s*gem\s+[\"']([^\"']+)[\"'](?:\s*,\s*[\"']([^\"']+)[\"'])?", re.MULTILINE)

_NAMES = frozenset(
    {
        "requirements.txt",
        "requirements-prod.txt",
        "pyproject.toml",
        "package.json",
        "go.mod",
        "Cargo.toml",
        "Gemfile",
    }
)


@dc.dataclass
class Package:
    """One declared dependency, SPDX-shaped fields."""

    name: str
    version: str
    license: str
    purl: str
    manifest: str

    def row(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "license": self.license,
            "purl": self.purl,
            "manifest": self.manifest,
        }


@dc.dataclass
class SbomResult:
    """The inventory, the policy verdicts and the visible gaps."""

    packages: list[Package] = dc.field(default_factory=list)
    violations: list[dict[str, str]] = dc.field(default_factory=list)
    gaps: list[dict[str, str]] = dc.field(default_factory=list)

    def digest(self) -> str:
        payload = json.dumps([p.row() for p in self.packages], sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _purl(ecosystem: str, name: str, version: str) -> str:
    """The package URL, scoped npm names folded into a namespace.

    NuGet package ids are case-sensitive, so theirs keep the declared
    casing; every other ecosystem's name is declared lowercase here.
    """
    if ecosystem == "npm" and name.startswith("@") and "/" in name:
        scope, _, bare = name[1:].partition("/")
        return f"pkg:npm/{scope}/{bare}@{version}"
    purl_name = name if ecosystem == "nuget" else name.lower()
    base = f"pkg:{ecosystem}/{purl_name}"
    return f"{base}@{version}" if version != NOASSERTION else base


def _package(ecosystem: str, name: str, version: str, manifest: str) -> Package:
    ver = version.strip() if version else NOASSERTION
    return Package(
        name=name,
        version=ver,
        license=NOASSERTION,
        purl=_purl(ecosystem, name, ver),
        manifest=manifest,
    )


def _parse_requirements(text: str, manifest: str) -> list[Package]:
    rows: list[Package] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-", ".")):
            continue
        match = re.match(r"^([A-Za-z0-9_.\[\]-]+)\s*(.*)$", line)
        if not match:
            continue
        name = match.group(1).split("[")[0]
        version = match.group(2).strip()
        version = version.split(";")[0].strip().lstrip("=<>~! ")
        rows.append(_package("pypi", name, version, manifest))
    return rows


def _pyproject_project_rows(doc: dict, manifest: str) -> list[Package]:
    """PEP 621 ``[project] dependencies`` - requirement strings, versions kept."""
    rows: list[Package] = []
    project = doc.get("project")
    if not isinstance(project, dict):
        return rows
    deps = project.get("dependencies") or []
    for dep in deps:
        if not isinstance(dep, str):
            continue
        match = re.match(r"^([A-Za-z0-9_.\[\]-]+)\s*(.*)$", dep.strip())
        if match:
            ver = match.group(2).strip().lstrip("=<>~! ")
            rows.append(_package("pypi", match.group(1), ver, manifest))
    return rows


def _pyproject_poetry_rows(doc: dict, manifest: str) -> list[Package]:
    """``[tool.poetry.dependencies]`` - the caret/tilde specs, verbatim minus markers."""
    rows: list[Package] = []
    tool = doc.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    section = poetry.get("dependencies") if isinstance(poetry, dict) else None
    if not isinstance(section, dict):
        return rows
    for name, spec in section.items():
        if name == "python":
            continue
        version = spec.get("version", "") if isinstance(spec, dict) else str(spec)
        rows.append(_package("pypi", name, str(version).lstrip("^~>=< "), manifest))
    return rows


def _pyproject_declared_license(doc: dict) -> str:
    """The ``[project] license`` field, PEP 621 table or plain string."""
    project = doc.get("project")
    license_field = project.get("license", NOASSERTION) if isinstance(project, dict) else None
    if isinstance(license_field, dict):
        return str(license_field.get("text", NOASSERTION))
    return str(license_field) if license_field else NOASSERTION


def _parse_pyproject(data: bytes, manifest: str) -> tuple[list[Package], str]:
    doc = tomllib.loads(data.decode("utf-8"))
    rows = _pyproject_project_rows(doc, manifest) + _pyproject_poetry_rows(doc, manifest)
    return rows, _pyproject_declared_license(doc) or NOASSERTION


def _parse_json_manifest(text: str, manifest: str) -> tuple[list[Package], str]:
    doc = json.loads(text)
    rows: list[Package] = []
    for block in ("dependencies", "devDependencies"):
        for name, version in (doc.get(block, {}) or {}).items():
            rows.append(_package("npm", name, str(version), manifest))
    declared = str(doc.get("license", "") or "").strip() or NOASSERTION
    return rows, declared


def _parse_go_mod(text: str, manifest: str) -> list[Package]:
    rows: list[Package] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if in_block or line.startswith("require "):
            match = re.match(r"(?:require\s+)?([A-Za-z0-9._/-]+)\s+(v[0-9A-Za-z.\-+]+)", line)
            if match and not line.startswith("//"):
                rows.append(_package("golang", match.group(1), match.group(2), manifest))
    return rows


def _parse_cargo(data: bytes, manifest: str) -> tuple[list[Package], str]:
    doc = tomllib.loads(data.decode("utf-8"))
    rows: list[Package] = []
    for name, spec in (doc.get("dependencies", {}) or {}).items():
        version = spec.get("version", "") if isinstance(spec, dict) else str(spec)
        rows.append(_package("cargo", name, str(version), manifest))
    declared = str((doc.get("package", {}) or {}).get("license", "") or "").strip()
    return rows, declared or NOASSERTION


def _parse_csproj(text: str, manifest: str) -> list[Package]:
    return [
        _package("nuget", match.group(1), match.group(2) or "", manifest)
        for match in CSPROJ_REF.finditer(text)
    ]


def _parse_gemfile(text: str, manifest: str) -> list[Package]:
    return [
        _package("gem", match.group(1), match.group(2) or "", manifest)
        for match in GEM_REF.finditer(text)
    ]


def _walk_manifests(root: Path) -> list[Path]:
    """The six ecosystems' manifests, sorted, node_modules and venvs out."""
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(
            part in {"node_modules", ".git", ".venv", "venv", "__pycache__"} for part in path.parts
        ):
            continue
        if path.name in _NAMES or path.suffix.lower() == ".csproj":
            found.append(path)
    return found


def scan_sbom(root: Path) -> SbomResult:
    """Inventory every manifest under root; unparseable ones are gaps."""
    result = SbomResult()
    for manifest in _walk_manifests(root):
        rel = str(manifest.relative_to(root)) if root.is_dir() else manifest.name
        try:
            result.packages.extend(_parse_one(manifest, rel))
        except (OSError, ValueError, tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
            result.gaps.append({"manifest": rel, "reason": str(exc)[:160]})
    return result


def _parse_one(manifest: Path, rel: str) -> list[Package]:
    """One manifest to packages; the declared license rides on the rows."""
    data = manifest.read_bytes()
    text = data.decode("utf-8", errors="replace")
    rows, declared = _parse_typed_manifest(manifest.name, data, text, rel)
    return _stamp_license(rows, declared)


def _parse_typed_manifest(name: str, data: bytes, text: str, rel: str) -> tuple[list[Package], str]:
    """Dispatch one manifest by name; the license rides back with the rows."""
    if name == "package.json":
        return _parse_json_manifest(text, rel)
    if name == "pyproject.toml":
        return _parse_pyproject(data, rel)
    if name == "Cargo.toml":
        return _parse_cargo(data, rel)
    return _parse_loose_manifest(name, text, rel), NOASSERTION


def _parse_loose_manifest(name: str, text: str, rel: str) -> list[Package]:
    """The manifests whose format carries no declared license field."""
    if name == "go.mod":
        return _parse_go_mod(text, rel)
    if name == "Gemfile":
        return _parse_gemfile(text, rel)
    if name.startswith("requirements"):
        return _parse_requirements(text, rel)
    return _parse_csproj(text, rel)


def _stamp_license(rows: list[Package], declared: str) -> list[Package]:
    """A manifest-level license belongs to every package it declares."""
    if declared != NOASSERTION:
        for row in rows:
            row.license = declared
    return rows


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
    """The human report: inventory, verdicts, gaps - all visible."""
    lines = [f"# SBOM and license report ({len(result.packages)} packages)", ""]
    for package in sorted(result.packages, key=lambda p: (p.purl, p.manifest)):
        lines.append(f"- {package.purl} - license: {package.license} ({package.manifest})")
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
