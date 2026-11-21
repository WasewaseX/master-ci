"""The per-ecosystem manifest readers behind the SBOM inventory (A19).

One module per concern the inventory cannot fake: what a manifest
declares, read by shape and by name - never by one textual layout.
The readers own the value object and the per-ecosystem rules:

- the row: one declared dependency - name, the declared version spec
    verbatim (``NOASSERTION`` when the manifest declares none; an
    exact ``==`` pin collapses to its version, a range keeps its
    operators, so ``>=1.0`` is never presented as ``==1.0`` or as a
    resolved pin), and a purl built by each ecosystem's identity
    rules. Declared, not resolved: the spec is what the manifest
    says, never a resolved graph - that boundary stays with the lock
    files and the existing dependency oracles.
- the identity rules: npm scoped names keep their scope with the
    required ``%40`` escape; NuGet ids and Go module paths are
    case-sensitive, so they keep the declared casing; the
    case-insensitive ecosystems (PyPI, Cargo, Ruby) fold to lowercase.
    A purl carries a version only when the spec pins exactly one - a
    range or a location spec is never presented as a resolved version.
- the sections: every declared dependency section is read - npm's
    ``dependencies``, ``devDependencies``, ``optionalDependencies``
    and ``peerDependencies``; PEP 621 ``[project] dependencies`` and
    ``optional-dependencies``; PEP 735 ``[dependency-groups]``;
    Poetry's main, ``dev-dependencies`` and group tables; Cargo's
    ``dependencies``, ``dev-dependencies``, ``build-dependencies``
    and their target-specific tables, with ``package =`` aliases
    resolved to the real crate. A section the reader skips is a
    dependency nobody judged.
- the shapes: manifests are parsed by element and attribute names
    (PackageReference rows: attribute order, quoting, extra
    attributes and child ``<Version>`` elements all parse), and a
    decoded document of the wrong shape raises ValueError - the
    recorded-gap class, never an AttributeError that stops the scan.
- the extras and markers: extras stay out of the name, environment
    markers stay out of the spec, inline comments are dropped - each
    piece is parsed by its own rule, so ``requests[socks]>=2.0;
    python_version<'3.11'`` names ``requests`` with the declared spec
    ``>=2.0`` and no marker.

A manifest's declared license is the license of the project the
manifest belongs to - it rides back beside the rows and is reported
separately; it is never stamped onto the packages the manifest
declares, because a dependency's license is not the project's.
"""

from __future__ import annotations

import dataclasses as dc
import json
import re
import tomllib
from pathlib import Path
from typing import Any

try:
    from defusedxml import ElementTree as SafeET  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - the scanner group ships defusedxml
    SafeET = None  # type: ignore[assignment]

NOASSERTION = "NOASSERTION"

# Manifests are untrusted: stdlib ET expands entity bombs, so csproj XML parses through defusedxml only - without it the reader fails closed.

# A bare pin: the spec names exactly one version (1.0, v0.9.1, 20).
# Anything carrying operators (`>=1.0`, `!=1.0`, `^1.2`, `~> 2.0`,
# `file:../x`, `*`) is a declaration or a location, never a resolved
# version - it keeps its shape in the row and never joins a purl.
_PIN_RE = re.compile(r"v?\d[\w.+-]*", re.IGNORECASE)
_EXACT_PIN_RE = re.compile(r"==\s*(v?\d[\w.+-]*)", re.IGNORECASE)

_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"\s*(?:\[(?P<extras>[^\]]*)\])?\s*(?P<spec>.*?)\s*$"
)
_REQ_FILE_RE = re.compile(r"requirements[\w.-]*\.txt", re.IGNORECASE)

GEM_REF = re.compile(r"^\s*gem\s+[\"']([^\"']+)[\"'](?:\s*,\s*[\"']([^\"']+)[\"'])?", re.MULTILINE)

_NAMES = frozenset({"pyproject.toml", "package.json", "go.mod", "Cargo.toml", "Gemfile"})

_NPM_SECTIONS = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
)

_CARGO_SECTIONS = ("dependencies", "dev-dependencies", "build-dependencies")


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


def declared_version(spec: str) -> str:
    """The version a manifest declares, unaltered.

    An exact ``==`` pin names one version, so it collapses to that
    version; every other spec keeps the operators it was declared with
    - ``demo!=1.0`` is not ``1.0``, ``demo>=1.0`` stays distinguishable
    from ``demo==1.0``, and Poetry's caret and tilde keep the
    constraint they were declared with. A spec that is only whitespace
    is no declaration: ``NOASSERTION``.
    """
    spec = spec.strip()
    if spec.startswith("(") and spec.endswith(")"):
        spec = spec[1:-1].strip()
    if not spec:
        return NOASSERTION
    exact = _EXACT_PIN_RE.fullmatch(spec)
    if exact:
        return exact.group(1)
    return spec


def purl(ecosystem: str, name: str, version: str) -> str:
    """The package URL, built by each ecosystem's identity rules."""
    if ecosystem == "npm":
        if name.startswith("@") and "/" in name:
            scope, _, bare = name[1:].partition("/")
            purl_name = f"%40{scope}/{bare}"
        else:
            purl_name = name
    elif ecosystem in ("nuget", "golang"):
        purl_name = name
    else:
        purl_name = name.lower()
    base = f"pkg:{ecosystem}/{purl_name}"
    if version != NOASSERTION and _PIN_RE.fullmatch(version):
        return f"{base}@{version}"
    return base


def _package(ecosystem: str, name: str, spec: str, manifest: str) -> Package:
    ver = declared_version(spec)
    return Package(
        name=name,
        version=ver,
        license=NOASSERTION,
        purl=purl(ecosystem, name, ver),
        manifest=manifest,
    )


def _requirement_parts(requirement: str) -> tuple[str, str] | None:
    """(name, declared spec) from one PEP 508 requirement string."""
    body = requirement.split(";", 1)[0]
    body = re.split(r"\s+#", body, maxsplit=1)[0].strip()
    match = _REQUIREMENT_RE.match(body)
    if not match or not match.group("name"):
        return None
    spec = (match.group("spec") or "").strip()
    if spec.startswith("(") and spec.endswith(")"):
        spec = spec[1:-1].strip()
    return match.group("name"), spec


def _parse_requirements(text: str, manifest: str) -> list[Package]:
    """requirements*.txt rows - constraints preserved, markers split off."""
    rows: list[Package] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-", ".")):
            continue
        parts = _requirement_parts(line)
        if parts:
            rows.append(_package("pypi", parts[0], parts[1], manifest))
    return rows


def _requirement_list_rows(items: object, manifest: str) -> list[Package]:
    """PEP 621-style requirement strings; only strings are declarations."""
    rows: list[Package] = []
    if not isinstance(items, list):
        return rows
    for dep in items:
        if isinstance(dep, str):
            parts = _requirement_parts(dep)
            if parts:
                rows.append(_package("pypi", parts[0], parts[1], manifest))
    return rows


def _pyproject_project_rows(doc: dict, manifest: str) -> list[Package]:
    """``[project] dependencies`` and ``optional-dependencies`` - all of it."""
    rows: list[Package] = []
    project = doc.get("project")
    if not isinstance(project, dict):
        return rows
    rows += _requirement_list_rows(project.get("dependencies"), manifest)
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for extra_deps in optional.values():
            rows += _requirement_list_rows(extra_deps, manifest)
    return rows


def _pyproject_group_rows(doc: dict, manifest: str) -> list[Package]:
    """PEP 735 ``[dependency-groups]`` - declared groups are inventory too."""
    rows: list[Package] = []
    groups = doc.get("dependency-groups")
    if not isinstance(groups, dict):
        return rows
    for group_deps in groups.values():
        rows += _requirement_list_rows(group_deps, manifest)
    return rows


def _poetry_section_rows(section: dict, manifest: str) -> list[Package]:
    """One Poetry dependency table; the python interpreter is not a package."""
    rows: list[Package] = []
    for name, spec in section.items():
        if name == "python" or not isinstance(name, str):
            continue
        version = ""
        if isinstance(spec, dict):
            version = str(spec.get("version") or "")
        elif isinstance(spec, str):
            version = spec
        rows.append(_package("pypi", name, version, manifest))
    return rows


def _pyproject_poetry_rows(doc: dict, manifest: str) -> list[Package]:
    """Poetry's dependency tables: main, ``dev-dependencies`` and groups."""
    rows: list[Package] = []
    tool = doc.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if not isinstance(poetry, dict):
        return rows
    for section_name in ("dependencies", "dev-dependencies"):
        section = poetry.get(section_name)
        if isinstance(section, dict):
            rows += _poetry_section_rows(section, manifest)
    groups = poetry.get("group")
    if isinstance(groups, dict):
        for group in groups.values():
            if isinstance(group, dict) and isinstance(group.get("dependencies"), dict):
                rows += _poetry_section_rows(group["dependencies"], manifest)
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
    rows = (
        _pyproject_project_rows(doc, manifest)
        + _pyproject_group_rows(doc, manifest)
        + _pyproject_poetry_rows(doc, manifest)
    )
    return rows, _pyproject_declared_license(doc) or NOASSERTION


def _json_project_license(value: object) -> str:
    """package.json's own license: string, ``{type}`` object or legacy list."""
    if isinstance(value, str):
        return value.strip() or NOASSERTION
    if isinstance(value, dict):
        return str(value.get("type", "") or "").strip() or NOASSERTION
    if isinstance(value, list):
        joined = " OR ".join(str(item).strip() for item in value if str(item).strip())
        return joined or NOASSERTION
    return NOASSERTION


def _parse_json_manifest(text: str, manifest: str) -> tuple[list[Package], str]:
    """package.json: every dependency section, shapes checked before use.

    A JSON object of the wrong shape - a list at the top, a dependency
    table that is an array - raises ValueError, which the scan records
    as a data gap; one bad manifest never stops the inventory. An
    explicit ``null`` section declares nothing.
    """
    doc = json.loads(text)
    if not isinstance(doc, dict):
        raise ValueError(
            f"{manifest}: package.json must be a JSON object, not {type(doc).__name__}"
        )
    rows: list[Package] = []
    for section in _NPM_SECTIONS:
        table = doc.get(section)
        if table is None:
            continue
        if not isinstance(table, dict):
            raise ValueError(
                f"{manifest}: {section} must be an object of name -> spec, not {type(table).__name__}"
            )
        for name, spec in table.items():
            rows.append(_package("npm", str(name), "" if spec is None else str(spec), manifest))
    return rows, _json_project_license(doc.get("license"))


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


def _cargo_spec(name: str, spec: object) -> tuple[str, str]:
    """(crate, declared spec): a dict's ``package`` names the real crate."""
    if isinstance(spec, dict):
        crate = str(spec.get("package") or name)
        version = str(spec.get("version") or "") if spec.get("version") else ""
        return crate, version
    return name, "" if spec is None else str(spec)


def _cargo_table_rows(table: object, manifest: str) -> list[Package]:
    rows: list[Package] = []
    if not isinstance(table, dict):
        return rows
    for name, spec in table.items():
        crate, version = _cargo_spec(str(name), spec)
        rows.append(_package("cargo", crate, version, manifest))
    return rows


def _parse_cargo(data: bytes, manifest: str) -> tuple[list[Package], str]:
    """Cargo.toml: main, dev, build and target tables; aliases resolved."""
    doc = tomllib.loads(data.decode("utf-8"))
    rows: list[Package] = []
    for section in _CARGO_SECTIONS:
        rows += _cargo_table_rows(doc.get(section), manifest)
    targets = doc.get("target")
    if isinstance(targets, dict):
        for target in targets.values():
            if not isinstance(target, dict):
                continue
            for section in _CARGO_SECTIONS:
                rows += _cargo_table_rows(target.get(section), manifest)
    package = doc.get("package")
    declared = ""
    if isinstance(package, dict):
        declared = str(package.get("license", "") or "").strip()
    return rows, declared or NOASSERTION


def _reference_version(element: Any) -> str:
    """The declared version: the attribute, or a child ``<Version>``."""
    version = element.get("Version") or ""
    if version:
        return version.strip()
    for child in element:
        if child.tag.rsplit("}", 1)[-1] == "Version":
            return (child.text or "").strip()
    return ""


def _parse_csproj(data: bytes, manifest: str) -> list[Package]:
    """PackageReference rows read by element and attribute names.

    Attribute order, quoting style, extra attributes (``PrivateAssets``)
    and a child ``<Version>`` element all parse - one textual layout is
    not a contract. Every failure class - not-XML, an unknown declared
    encoding (``LookupError``, not ``ParseError``), entities the safe
    reader refuses - lands on ValueError, the recorded-gap class.
    Without defusedxml the manifest is a gap too: entity expansion
    enabled is never the fallback.
    """
    if SafeET is None:
        raise ValueError(
            f"{manifest}: no safe XML reader installed (defusedxml missing), entity expansion refused"
        )
    try:
        root = SafeET.fromstring(data)
    except (SafeET.ParseError, ValueError, LookupError) as exc:
        # defusedxml's refusals (EntitiesForbidden, DTDForbidden) subclass
        # ValueError, so the entity-declaration class lands here as well
        raise ValueError(f"{manifest}: not parseable XML: {exc}") from exc
    rows: list[Package] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "PackageReference":
            continue
        name = element.get("Include")
        if not name:
            continue
        rows.append(_package("nuget", name.strip(), _reference_version(element), manifest))
    return rows


def _parse_gemfile(text: str, manifest: str) -> list[Package]:
    return [
        _package("gem", match.group(1), match.group(2) or "", manifest)
        for match in GEM_REF.finditer(text)
    ]


def _parse_loose_manifest(name: str, text: str, data: bytes, rel: str) -> list[Package]:
    """The manifests whose format carries no project-license field."""
    if name == "go.mod":
        return _parse_go_mod(text, rel)
    if name == "Gemfile":
        return _parse_gemfile(text, rel)
    if _REQ_FILE_RE.fullmatch(name):
        return _parse_requirements(text, rel)
    return _parse_csproj(data, rel)


def parse_typed_manifest(name: str, data: bytes, text: str, rel: str) -> tuple[list[Package], str]:
    """Dispatch one manifest by name; the project license rides back."""
    if name == "package.json":
        return _parse_json_manifest(text, rel)
    if name == "pyproject.toml":
        return _parse_pyproject(data, rel)
    if name == "Cargo.toml":
        return _parse_cargo(data, rel)
    return _parse_loose_manifest(name, text, data, rel), NOASSERTION


def walk_manifests(root: Path) -> list[Path]:
    """The six ecosystems' manifests, sorted, node_modules and venvs out.

    ``requirements*.txt`` is one filename pattern - ``requirements-dev.txt``
    and ``requirements-test.txt`` are discovered the same as
    ``requirements.txt``, and their dependencies reach the license
    policy like any other manifest's.
    """
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(
            part in {"node_modules", ".git", ".venv", "venv", "__pycache__"} for part in path.parts
        ):
            continue
        if (
            path.name in _NAMES
            or _REQ_FILE_RE.fullmatch(path.name)
            or path.suffix.lower() == ".csproj"
        ):
            found.append(path)
    return found
