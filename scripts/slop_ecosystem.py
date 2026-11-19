"""Cross-file ecosystem checks adopted from scanaislop (round-16 parity).

    Two oracles the per-file pipeline cannot express because they need
    the whole repository as evidence:

    - unused-css: custom class selectors defined in .css/.scss
        stylesheets but referenced nowhere in the project (className
        bindings, clsx/cn template literals, data-* hooks, HTML).
        Conservative by design: any class whose name appears as a
        substring in any scanned source string is spared, and
        utility-looking names are skipped.
    - vulnerable-dependency: converts a pip-audit JSON report into
        findings. The audit itself is opt-in (MASTERCI_PIP_AUDIT=1 with
        pip-audit installed); a skipped audit stays transparent at the
        gate layer via analysis-coverage instead of a per-file guess.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from slop_model import Finding

if TYPE_CHECKING:
    pass

CSS_EXTS = {".css", ".scss"}
# Utility-looking names (Tailwind-style) are skipped: `hover:bg-red-500`,
# `w-[42px]`, `md:flex`. A custom component class never contains these.
_UTILITY_NAME = re.compile(r"[\[:/]|^-|--|^\d")
# The class name runs until a whitespace/brace boundary, and the colon
# rides with it: `md:flex` is a Tailwind variant, `.card:hover` a pseudo
# class - both carry the colon into the captured name. The backslash is
# included so the escaped-colon form `md\:flex` stays one name.
_CLASS_SELECTOR = re.compile(r"(?<![\w#-])\.([A-Za-z_][\w:\\-]*)")
# SCSS interpolation and nesting parents produce selector fragments that
# are not real class names; `#{$name}` and `&-suffix` are excluded here.
_SCSS_NOISE = re.compile(r"[&%]")
MAX_FINDINGS_PER_CHECK = 5

_AUDIT_MANIFESTS = ("pyproject.toml", "requirements.txt", "requirements-dev.txt")


def _stylesheet_class_names(text: str) -> set[str]:
    """Class selectors in one stylesheet, minus SCSS noise and utilities."""
    names: set[str] = set()
    for line in text.splitlines():
        if "/*" in line and "*/" in line:
            line = line.split("/*", 1)[0] + line.split("*/", 1)[1]
        for m in _CLASS_SELECTOR.finditer(line):
            name = m.group(1)
            if _UTILITY_NAME.search(name) or _SCSS_NOISE.search(name):
                continue
            names.add(name)
    return names


def check_unused_css(root: Path, files: list[Path], globs: list[str]) -> list[Finding]:
    """Classes defined in stylesheets but referenced nowhere (advisory)."""
    if not root.is_dir():
        return []
    from slop_files import is_ignored

    styles: dict[str, tuple[Path, set[str], str]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in CSS_EXTS:
            continue
        rel = path.relative_to(root).as_posix()
        if is_ignored(rel, globs) or "node_modules/" in f"/{rel}":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        styles[rel] = (path, _stylesheet_class_names(text), text)
    if not styles:
        return []

    # The usage corpus is every scanned non-stylesheet file. Stylesheets
    # are not in it: a class is trivially present in its own selector
    # text, and iter_files may hand the .css files over with the rest -
    # the discovery glob above already owns them.
    corpus: list[str] = []
    for path in files:
        if path.suffix.lower() in CSS_EXTS:
            continue
        try:
            corpus.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue

    findings: list[Finding] = []
    for rel, (path, names, _own_text) in sorted(styles.items()):
        # The raw text of the OTHER sheets is evidence: a class consumed
        # by a sibling sheet (variants, compositions) is alive.
        other_sheet_text = [text for r, (_, _, text) in styles.items() if r != rel]
        evidence = corpus + other_sheet_text
        for name in sorted(names):
            # Substring evidence anywhere keeps the class: dynamic className
            # builders (`ui-${variant}` prefixes) are invisible to a syntax
            # oracle, so the rule errs alive.
            if any(name in blob for blob in evidence):
                continue
            findings.append(
                Finding(
                    path=str(path),
                    line=1,
                    check="unused-css",
                    message=(
                        f"CSS class .{name} is defined in {rel} but referenced "
                        "nowhere in the project - delete it or wire it up"
                    ),
                    weight=1,
                )
            )
            if len(findings) >= MAX_FINDINGS_PER_CHECK:
                return findings
    return findings


# -- vulnerable-dependency ---------------------------------------------------


def _audit_command() -> list[str] | None:
    """The pip-audit invocation when the opt-in is set and the tool exists."""
    if os.environ.get("MASTERCI_PIP_AUDIT", "") != "1":
        return None
    tool = shutil.which("pip-audit")
    return None if not tool else [tool, "--format", "json"]


def _parse_audit_report(stdout: str) -> list[tuple[str, str]]:
    """(package, vulnerability id) pairs the audit flagged."""
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        return []
    out: list[tuple[str, str]] = []
    for dep in payload.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            out.append((dep.get("name", "?"), vuln.get("id", "?")))
    return out


def check_vulnerable_dependencies(
    root: Path, files: list[Path], globs: list[str]
) -> list[Finding]:
    """Findings from an opt-in pip-audit run over the project manifests."""
    if not root.is_dir():
        return []
    from slop_files import is_ignored

    has_manifest = any(
        (root / name).is_file() and not is_ignored(name, globs)
        for name in _AUDIT_MANIFESTS
    )
    if not has_manifest:
        return []
    cmd = _audit_command()
    if cmd is None:
        # The audit did not run: no finding here. The gate layer reports
        # skipped analysis once (analysis-coverage), never per-file.
        return []
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    findings: list[Finding] = []
    for package, vuln_id in _parse_audit_report(proc.stdout):
        findings.append(
            Finding(
                path=str(root / "pyproject.toml"),
                line=1,
                check="vulnerable-dependency",
                message=(
                    f"{package}: known vulnerability {vuln_id} (pip-audit) - "
                    "upgrade to a patched release"
                ),
                weight=5,
            )
        )
    return findings
