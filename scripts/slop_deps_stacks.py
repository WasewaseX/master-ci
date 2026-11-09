"""The v2.1 dependency stacks: Go, Rust, PHP, C# and Java.

The second half of the dependency-health tranche (slop_deps.py carries
the shared plumbing and the Python/Node oracles). Each ecosystem gets
the same conservative question - does any source under the manifest's
directory consume the declaration? - with the exemptions its toolchain
actually requires:

- Go:   go.mod ``require`` - direct lines only, ``// indirect`` is
        transitive weight the toolchain owns. Any quoted path under
        the module prefix in a .go file is use.
- Rust: Cargo.toml ``[dependencies]`` - dev/build/workspace tables and
        ``[target.'...'.dependencies]`` are exempt. Dash and
        underscore spellings both count, and a ``package =`` rename is
        use under either name.
- PHP:  composer.json ``require`` (``require-dev`` exempt). Composer
        loads polyfill packages without a named reference, so they are
        exempt too, as are the ``php``/``ext-*``/``lib-*`` platform
        constraints. Use is a mention of the vendor namespace - PSR-4
        means a consumed package is referenced by it.
- C#:   .csproj ``PackageReference Include=`` against .cs sources.
        ``Update=`` is version management, not a dependency. The
        longest token of the package id must appear in some source
        file; build integration (analyzers, runners, test SDKs, source
        generators, coverlet, SourceLink) never appears in ``using``
        statements and is exempt.
- Java: pom.xml direct-child ``<dependencies>`` (so
        ``<dependencyManagement>`` version catalogs and build plugins
        stay out), compile scope only - test/provided are not the
        app's weight and runtime is where the SPI drivers live. Any
        artifactId token found in a JVM import or FQCN is use.

Deliberately out of scope: Ruby (Bundler.require auto-loads every
default-group gem at boot, so a missing require line is not evidence
of anything) and C/C++ (CMake target names do not determine include
paths; the false-positive rate would breach the conservative trade).
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from slop_deps import cap_findings, manifests, scoped, texts
from slop_model import Finding

_GO_EXTS = (".go",)
_RS_EXTS = (".rs",)
_PHP_EXTS = (".php",)
_CS_EXTS = (".cs",)
_JVM_EXTS = (".java", ".kt", ".kts", ".scala", ".groovy")

# Build-integration C# packages the compiler consumes so the source never
# names them: analyzers, runners, test platform, coverage, source link.
_CS_EXEMPT = re.compile(
    r"(?:\.(?:analyzers?|build\.tasks|targets|props|test\.sdk|ref)$"
    r"|coverlet|microsoft\.srcelink|\.(?:runner|generators?)\.)",
    re.IGNORECASE,
)

_XML_SCOPES_EXEMPT = {"test", "provided", "runtime", "system"}


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


def _go_requires(manifest: Path) -> dict[str, int]:
    """go.mod direct requires -> {module path: declaration line}.

    ``// indirect`` lines are transitive weight the toolchain owns, and
    ``tool``/``exclude``/``replace`` blocks are not app dependencies.
    """
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    deps: dict[str, int] = {}
    in_require = False
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if in_require:
            if stripped.startswith(")"):
                in_require = False
            elif stripped and not stripped.startswith("//") and "// indirect" not in stripped:
                parts = stripped.split()
                if len(parts) >= 2:
                    deps[parts[0]] = lineno
            continue
        if stripped.startswith("require "):
            rest = stripped[len("require ") :].strip()
            if rest.startswith("("):
                in_require = True
            elif "// indirect" not in stripped:
                parts = rest.split()
                if len(parts) >= 2:
                    deps[parts[0]] = lineno
    return deps


def _go_unused_dep_findings(go_deps: dict[str, int], files: list[Path], rel: str) -> list[Finding]:
    paths: set[str] = set()
    for _, text in texts(files, _GO_EXTS).items():
        paths.update(re.findall(r'"([^"]+)"', text))
    findings: list[Finding] = []
    for module, lineno in go_deps.items():
        if any(p == module or p.startswith(module + "/") for p in paths):
            continue
        findings.append(
            Finding(
                path=rel,
                line=lineno,
                check="unused-dependency",
                message=(
                    f"required module {module!r} is never imported - "
                    "declared weight with no consumer"
                ),
                weight=1,
                advisory=True,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------


def _rust_deps(manifest: Path) -> dict[str, tuple[int, str | None]]:
    """Cargo.toml [dependencies] -> {crate: (line, package rename)}.

    Only the plain ``[dependencies]`` table counts: dev/build tables are
    test and toolchain weight, ``[workspace.dependencies]`` is a version
    catalog, and per-target tables are conditional by definition.
    """
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    deps: dict[str, tuple[int, str | None]] = {}
    in_deps = False
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("["):
            in_deps = stripped == "[dependencies]"
            continue
        if not in_deps or not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9_.\-]+)\s*=", stripped)
        if not match:
            continue
        rename = re.search(r'package\s*=\s*"([^"]+)"', stripped)
        deps[match.group(1)] = (lineno, rename.group(1) if rename else None)
    return deps


def _rust_unused_dep_findings(
    rs_deps: dict[str, tuple[int, str | None]], files: list[Path], rel: str
) -> list[Finding]:
    rs_texts = list(texts(files, _RS_EXTS).values())
    findings: list[Finding] = []
    for crate, (lineno, rename) in rs_deps.items():
        variants = {crate, crate.replace("-", "_")}
        if rename:
            variants.update({rename, rename.replace("-", "_")})
        rx = re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, variants))) + r")\b")
        if any(rx.search(text) for text in rs_texts):
            continue
        findings.append(
            Finding(
                path=rel,
                line=lineno,
                check="unused-dependency",
                message=(
                    f"crate {crate!r} is never referenced in Rust sources - "
                    "declared weight with no consumer"
                ),
                weight=1,
                advisory=True,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------


def _php_requires(manifest: Path) -> dict[str, int]:
    """composer.json require -> {package: declaration line}.

    require-dev is test tooling. ``php``/``ext-*``/``lib-*`` are
    platform constraints, and polyfills are loaded by composer itself,
    never by a named reference.
    """
    try:
        raw = manifest.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    require = data.get("require")
    if not isinstance(require, dict):
        return {}
    deps: dict[str, int] = {}
    for pkg in require:
        if pkg in ("php", "hhvm") or pkg.startswith(("ext-", "lib-")):
            continue
        if "polyfill" in pkg:
            continue
        match = re.search(rf"[\"']{re.escape(pkg)}[\"']\s*:", raw)
        if match:
            deps[pkg] = raw[: match.start()].count("\n") + 1
    return deps


def _php_unused_dep_findings(
    php_deps: dict[str, int], files: list[Path], rel: str
) -> list[Finding]:
    php_texts = list(texts(files, _PHP_EXTS).values())
    findings: list[Finding] = []
    for pkg, lineno in php_deps.items():
        vendor = pkg.split("/")[0]
        rx = re.compile(r"\b" + re.escape(vendor) + r"\b", re.IGNORECASE)
        if any(rx.search(text) for text in php_texts):
            continue
        findings.append(
            Finding(
                path=rel,
                line=lineno,
                check="unused-dependency",
                message=(
                    f"required package {pkg!r} is never referenced by its "
                    "vendor namespace - declared weight with no consumer"
                ),
                weight=1,
                advisory=True,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# C#
# ---------------------------------------------------------------------------


def _cs_packages(manifest: Path) -> dict[str, int]:
    """csproj PackageReference Include= -> {package id: line}.

    ``Update=`` entries are central version management, not dependencies.
    """
    try:
        raw = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    deps: dict[str, int] = {}
    for match in re.finditer(r'<PackageReference\s+Include="([^"]+)"', raw):
        deps[match.group(1)] = raw[: match.start()].count("\n") + 1
    return deps


def _cs_unused_dep_findings(cs_deps: dict[str, int], files: list[Path], rel: str) -> list[Finding]:
    cs_texts = list(texts(files, _CS_EXTS).values())
    findings: list[Finding] = []
    for pkg, lineno in cs_deps.items():
        if _CS_EXEMPT.search(pkg):
            continue
        tokens = [t for t in re.split(r"[.\-]", pkg) if len(t) >= 4 and t.isalpha()]
        if not tokens:
            continue  # nothing judgable - the conservative call is silence
        probe = max(tokens, key=len)
        rx = re.compile(r"\b" + re.escape(probe) + r"\b", re.IGNORECASE)
        if any(rx.search(text) for text in cs_texts):
            continue
        findings.append(
            Finding(
                path=rel,
                line=lineno,
                check="unused-dependency",
                message=(
                    f"package {pkg!r} is never referenced by its name token - "
                    "declared weight with no consumer"
                ),
                weight=1,
                advisory=True,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Java / JVM
# ---------------------------------------------------------------------------


def _xml_children(element: ET.Element, name: str) -> list[ET.Element]:
    """Namespace-agnostic direct children with the given local tag name."""
    return [child for child in element if child.tag.rsplit("}", 1)[-1] == name]


def _xml_text(element: ET.Element, name: str) -> str:
    kids = _xml_children(element, name)
    return kids[0].text.strip() if kids and kids[0].text else ""


def _java_artifacts(pom: Path) -> dict[str, int]:
    """pom.xml compile-scope dependencies -> {artifactId: line}.

    Only direct-child ``<dependencies>`` of the project element count:
    ``<dependencyManagement>`` is a version catalog and build-plugin
    dependencies are toolchain. Runtime/provided scopes are SPI and
    container weight, not the app's declared imports.
    """
    try:
        raw = pom.read_text(encoding="utf-8", errors="replace")
        tree = ET.fromstring(raw)
    except (OSError, ET.ParseError, UnicodeDecodeError):
        return {}
    deps: dict[str, int] = {}
    for section in _xml_children(tree, "dependencies"):
        for dep in _xml_children(section, "dependency"):
            name = _xml_text(dep, "artifactId")
            if not name or _xml_text(dep, "scope") in _XML_SCOPES_EXEMPT:
                continue
            match = re.search(rf"<artifactId>{re.escape(name)}</artifactId>", raw)
            deps[name] = raw[: match.start()].count("\n") + 1 if match else 1
    return deps


def _java_unused_dep_findings(
    jvm_deps: dict[str, int], files: list[Path], rel: str
) -> list[Finding]:
    jvm_texts = list(texts(files, _JVM_EXTS).values())
    findings: list[Finding] = []
    for artifact, lineno in jvm_deps.items():
        tokens = [t for t in re.split(r"[-_.]", artifact) if len(t) >= 3 and not t.isdigit()]
        if not tokens:
            continue  # nothing judgable - the conservative call is silence
        rx = re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, tokens))) + r")\b")
        if any(rx.search(text) for text in jvm_texts):
            continue
        findings.append(
            Finding(
                path=rel,
                line=lineno,
                check="unused-dependency",
                message=(
                    f"dependency {artifact!r} is never referenced by any of its "
                    "name tokens - declared weight with no consumer"
                ),
                weight=1,
                advisory=True,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def check_stack_dependencies(root: Path, files: list[Path], globs: list[str]) -> list[Finding]:
    """The Go, Rust, PHP, C# and Java dependency oracles (advisory)."""
    if not root.is_dir():
        return []
    findings: list[Finding] = []
    for manifest, rel in manifests(root, "go.mod", globs):
        deps = _go_requires(manifest)
        findings.extend(
            _go_unused_dep_findings(deps, scoped(files, manifest.parent), rel.as_posix())
        )
    for manifest, rel in manifests(root, "Cargo.toml", globs):
        rs_deps = _rust_deps(manifest)
        findings.extend(
            _rust_unused_dep_findings(rs_deps, scoped(files, manifest.parent), rel.as_posix())
        )
    for manifest, rel in manifests(root, "composer.json", globs):
        deps = _php_requires(manifest)
        findings.extend(
            _php_unused_dep_findings(deps, scoped(files, manifest.parent), rel.as_posix())
        )
    for manifest, rel in manifests(root, "*.csproj", globs):
        deps = _cs_packages(manifest)
        findings.extend(
            _cs_unused_dep_findings(deps, scoped(files, manifest.parent), rel.as_posix())
        )
    for manifest, rel in manifests(root, "pom.xml", globs):
        deps = _java_artifacts(manifest)
        findings.extend(
            _java_unused_dep_findings(deps, scoped(files, manifest.parent), rel.as_posix())
        )
    return cap_findings(findings)
