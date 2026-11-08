"""Opt-in architecture rules: the repo's declared law, enforced.

Configured by a `.ai-integrity-arch.json` at the scan root (scanaislop
docs, "Custom Architecture Rules"): forbidden imports, layer separation
and required patterns. No config file means no findings - the rules
exist only where a project declares them, and a declared rule is a
contract, not a judgment call: violations are blocking-grade.

The configuration itself is fail-closed (v1.18): a config that exists
but cannot be honored raises :class:`ArchConfigError`, which the CLI
turns into exit code 2. Silently skipping declared rules would tell the
author their law is enforced while enforcing nothing.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path

from slop_model import Finding, _finding

ARCH_CONFIG_NAME = ".ai-integrity-arch.json"
PY_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", re.MULTILINE)
JS_IMPORT_RE = re.compile(
    r"(?:import\s+[^'\"]*?from\s+|import\s+|require\s*\(\s*)[\"']([^\"']+)[\"']"
)


class ArchConfigError(ValueError):
    """The architecture config exists but cannot be honored - fail closed."""


_ALLOWED_KEYS = frozenset({"forbidden_imports", "layers", "required_patterns"})
_MODULE_RE = re.compile(r"[@\w][\w./-]*")


@dataclass(frozen=True)
class LayerRule:
    name: str
    paths: tuple[str, ...]
    must_not_import: tuple[str, ...]


@dataclass(frozen=True)
class PatternRule:
    glob: str
    pattern: re.Pattern[str]
    reason: str


@dataclass(frozen=True)
class ArchConfig:
    forbidden: tuple[tuple[str, str], ...]
    layers: tuple[LayerRule, ...]
    required: tuple[PatternRule, ...]


def _rule_list(data: dict, key: str) -> list:
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not value:
        raise ArchConfigError(f"{key} must be a non-empty list of rule objects")
    return value


def _text(rule: dict, key: str, where: str) -> str:
    value = rule.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ArchConfigError(f"{where}: {key!r} must be a non-empty string")
    return value


def _parse_forbidden(data: dict) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    for idx, rule in enumerate(_rule_list(data, "forbidden_imports"), 1):
        where = f"forbidden_imports[{idx}]"
        if not isinstance(rule, dict):
            raise ArchConfigError(f"{where} must be an object")
        module = _text(rule, "module", where)
        if not _MODULE_RE.fullmatch(module):
            raise ArchConfigError(f"{where}: not a module or package name: {module!r}")
        reason = rule.get("reason", "forbidden by architecture config")
        if not isinstance(reason, str):
            raise ArchConfigError(f"{where}: reason must be a string")
        out.append((module, reason))
    return tuple(out)


def _parse_layers(data: dict) -> tuple[LayerRule, ...]:
    out: list[LayerRule] = []
    for idx, layer in enumerate(_rule_list(data, "layers"), 1):
        where = f"layers[{idx}]"
        if not isinstance(layer, dict):
            raise ArchConfigError(f"{where} must be an object")
        name = _text(layer, "name", where)
        paths = layer.get("paths")
        if (
            not isinstance(paths, list)
            or not paths
            or not all(isinstance(p, str) and p.strip() for p in paths)
        ):
            raise ArchConfigError(f"{where}: paths must be a non-empty list of globs")
        deny = layer.get("must_not_import", [])
        if not isinstance(deny, list) or not all(isinstance(d, str) and d.strip() for d in deny):
            raise ArchConfigError(f"{where}: must_not_import must be a list of layer names")
        out.append(LayerRule(name, tuple(paths), tuple(deny)))
    # A must_not_import naming a layer nobody declares can never match, so
    # the rule would be inert while looking enforced - reject it here.
    declared = {layer.name for layer in out}
    for layer in out:
        unknown = sorted(set(layer.must_not_import) - declared)
        if unknown:
            raise ArchConfigError(
                f"layer {layer.name!r}: must_not_import names undeclared "
                f"layer(s): {', '.join(unknown)}"
            )
    return tuple(out)


def _parse_required(data: dict) -> tuple[PatternRule, ...]:
    out: list[PatternRule] = []
    for idx, rule in enumerate(_rule_list(data, "required_patterns"), 1):
        where = f"required_patterns[{idx}]"
        if not isinstance(rule, dict):
            raise ArchConfigError(f"{where} must be an object")
        glob = _text(rule, "glob", where)
        pattern = _text(rule, "pattern", where)
        try:
            compiled = re.compile(pattern, re.MULTILINE)
        except re.error as exc:
            raise ArchConfigError(f"{where}: bad regex {pattern!r}: {exc}") from exc
        out.append(PatternRule(glob, compiled, rule.get("reason") or ""))
    return tuple(out)


def load_arch_config(root: Path) -> ArchConfig | None:
    """Read `.ai-integrity-arch.json`; None when absent.

    Fail-closed: present but unreadable, invalid, unknown-keyed or
    rule-less configs raise :class:`ArchConfigError`.
    """
    path = root / ARCH_CONFIG_NAME
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ArchConfigError(f"{ARCH_CONFIG_NAME}: unreadable: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ArchConfigError(f"{ARCH_CONFIG_NAME}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ArchConfigError(f"{ARCH_CONFIG_NAME}: top level must be an object")
    unknown = sorted(set(data) - _ALLOWED_KEYS)
    if unknown:
        hint = ", ".join(sorted(_ALLOWED_KEYS))
        raise ArchConfigError(f"{ARCH_CONFIG_NAME}: unknown key {unknown[0]!r}; allowed: {hint}")
    cfg = ArchConfig(_parse_forbidden(data), _parse_layers(data), _parse_required(data))
    if not (cfg.forbidden or cfg.layers or cfg.required):
        raise ArchConfigError(
            f"{ARCH_CONFIG_NAME} declares no rules; delete the file or declare "
            "forbidden_imports, layers or required_patterns"
        )
    return cfg


def _code_rels(root: Path, files: list[Path], globs: list[str]) -> list[tuple[Path, str]]:
    from slop_files import is_ignored  # local: avoid a cycle at import time

    rels: list[tuple[Path, str]] = []
    for path in files:
        if path.suffix.lower() not in {".py", ".js", ".ts", ".jsx", ".tsx"}:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if is_ignored(rel, globs):
            continue
        rels.append((path, rel))
    return rels


def _import_names(rel: str, text: str) -> list[tuple[str, int]]:
    """(top-level import name, offset) pairs for one file."""
    if rel.endswith(".py"):
        return [(m.group(1).split(".")[0], m.start()) for m in PY_IMPORT_RE.finditer(text)]
    if rel.endswith((".js", ".ts", ".jsx", ".tsx")):
        out: list[tuple[str, int]] = []
        for m in JS_IMPORT_RE.finditer(text):
            spec = m.group(1)
            if spec.startswith((".", "/", "node:")):
                continue
            pkg = spec if spec.startswith("@") else spec.split("/")[0]
            out.append((pkg, m.start()))
        return out
    return []


def _layer_of(rel: str, cfg: ArchConfig) -> str | None:
    for layer in cfg.layers:
        if any(fnmatch.fnmatch(rel, glob) for glob in layer.paths):
            return layer.name
    return None


def _module_layer_map(root: Path, cfg: ArchConfig, rels: list[tuple[Path, str]]) -> dict[str, str]:
    """Import-name -> layer-name, from the files each layer's paths cover.

    Registers both the module stem and the parent directory name, so
    `from ui import render` resolves to the ui layer, not just
    `import view` does."""
    mapping: dict[str, str] = {}
    for _, rel in rels:
        layer = _layer_of(rel, cfg)
        if layer is None:
            continue
        mapping.setdefault(Path(rel).stem, layer)
        parent = Path(rel).parent.name
        if parent:
            mapping.setdefault(parent, layer)
    return mapping


def _forbidden_findings(cfg: ArchConfig, rels: list[tuple[Path, str]]) -> list[Finding]:
    findings: list[Finding] = []
    for path, rel in rels:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, pos in _import_names(rel, text):
            for module, reason in cfg.forbidden:
                if name == module or name.startswith(module + "."):
                    findings.append(
                        _finding(
                            path,
                            text,
                            pos,
                            "arch-forbidden-import",
                            f"import {name!r}: {reason}",
                            2,
                        )
                    )
    return findings


def _layer_findings(
    cfg: ArchConfig, rels: list[tuple[Path, str]], name_layer: dict[str, str]
) -> list[Finding]:
    rules = {layer.name: layer for layer in cfg.layers}
    findings: list[Finding] = []
    for path, rel in rels:
        holder = _layer_of(rel, cfg)
        if holder is None or not rules[holder].must_not_import:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, pos in _import_names(rel, text):
            target = name_layer.get(name)
            if target and target in rules[holder].must_not_import:
                findings.append(
                    _finding(
                        path,
                        text,
                        pos,
                        "arch-layer-violation",
                        f"layer {holder!r} imports layer {target!r} ({name})",
                        2,
                    )
                )
    return findings


def _required_findings(cfg: ArchConfig, rels: list[tuple[Path, str]]) -> list[Finding]:
    findings: list[Finding] = []
    for path, rel in rels:
        for rule in cfg.required:
            if not fnmatch.fnmatch(rel, rule.glob):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not rule.pattern.search(text):
                suffix = f" - {rule.reason}" if rule.reason else ""
                findings.append(
                    Finding(
                        path=str(path),
                        line=1,
                        check="arch-missing-pattern",
                        message=f"required pattern {rule.pattern!r} not found{suffix}",
                        weight=2,
                    )
                )
    return findings


def check_architecture_rules(root: Path, files: list[Path], globs: list[str]) -> list[Finding]:
    """Enforce the declared architecture, once per scan."""
    base = root if root.is_dir() else root.parent
    cfg = load_arch_config(base)
    if cfg is None:
        return []
    rels = _code_rels(base, files, globs)
    name_layer = _module_layer_map(base, cfg, rels)
    return (
        _forbidden_findings(cfg, rels)
        + _layer_findings(cfg, rels, name_layer)
        + _required_findings(cfg, rels)
    )
