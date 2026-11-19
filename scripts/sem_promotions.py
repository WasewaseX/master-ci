"""The Semaphore promotions model, reverse-engineered (round-16).

Semaphore's signature feature is the promotion: a named target that
starts a downstream pipeline after (or during) the parent run, with
declared parameters and an auto-promotion condition. This module
ports the model to GitHub Actions reality:

- `.semaphore/promotions.yml` declares the promotions as repo data:
    name, target workflow file, auto_promote condition, parameters
    with required/default/options/regex validation.
- `validate` enforces the contracts: unique names, existing target
    files, the conditions-DSL subset (`result`, `result_reason`,
    `branch`, `tag`, `pull_request`, operators =, !=, =~, !~, and,
    or), `result` only legal inside auto_promote, required parameters
    carrying a default when the promotion auto-promotes, and
    regex_pattern compiling.
- `promote` validates the supplied parameters against the declared
    contract and prints the exact `gh workflow run` command it would
    execute - the manual-promotion path with no secret ever printed.

Exit codes: 0 valid/promoted, 1 validation failure, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    pass

DEFAULT_CONFIG = ".semaphore/promotions.yml"
ALLOWED_CONDITION_WORDS = frozenset(
    {"result", "result_reason", "branch", "tag", "pull_request", "change_in", "and", "or"}
)
CONDITION_OPS = re.compile(r"(=~|!~|!=|=|\band\b|\bor\b|\bAND\b|\bOR\b)")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_PARAM_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
PROMOTION_KEYS = frozenset({"name", "pipeline_file", "auto_promote", "parameters"})
PARAM_KEYS = frozenset(
    {"name", "description", "required", "default_value", "options", "regex_pattern"}
)


class PromotionError(ValueError):
    """A promotions.yml defect or a failed parameter validation."""


def load_promotions(path: Path) -> list[dict]:
    """The promotions list from one config file, with key validation."""
    import yaml

    if not path.is_file():
        raise PromotionError(f"promotions config not found: {path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PromotionError(f"promotions config is not valid YAML: {exc}") from exc
    entries = payload.get("promotions")
    if not isinstance(entries, list) or not entries:
        raise PromotionError("promotions config must carry a non-empty 'promotions' list")
    for entry in entries:
        unknown = set(entry) - PROMOTION_KEYS
        if unknown:
            raise PromotionError(
                f"promotion {entry.get('name', '?')}: unknown keys {sorted(unknown)}"
            )
    return entries


def validate_condition(expr: str, *, auto: bool) -> list[str]:
    """The conditions-DSL subset errors for one `when` expression.

    Only the documented keywords and comparison operators are legal;
    `result`/`result_reason` are post-run facts and therefore legal only
    inside auto_promote.
    """
    errors: list[str] = []
    # Quoted literals are string values, not keywords: 'passed' and
    # 'main' must not be read as condition vocabulary.
    for word in re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*", _QUOTED.sub("", expr)):
        if word in ALLOWED_CONDITION_WORDS:
            continue
        errors.append(f"unknown condition keyword '{word}' in: {expr}")
    if not CONDITION_OPS.search(expr) and expr.strip():
        errors.append(f"condition has no comparison or boolean operator: {expr}")
    if not auto and re.search(r"\b(result|result_reason)\b", expr):
        errors.append("'result'/'result_reason' are only legal inside auto_promote")
    return errors


def _validate_auto_promote(entry: dict) -> list[str]:
    """The auto_promote block's own contract errors."""
    auto = entry.get("auto_promote")
    if auto is None:
        return []
    when = auto.get("when") if isinstance(auto, dict) else None
    if not when:
        return ["auto_promote requires a 'when' condition"]
    return validate_condition(str(when), auto=True)


def _validate_parameters_block(entry: dict) -> list[str]:
    """The declared env_vars' shape errors: names, defaults, regexes."""
    errors: list[str] = []
    auto = entry.get("auto_promote")
    params = entry.get("parameters") or {}
    for param in params.get("env_vars", []) if isinstance(params, dict) else []:
        pname = str(param.get("name"))
        errors.extend(_unknown_param_keys(pname, param))
        if not _PARAM_NAME.fullmatch(str(pname)):
            errors.append(f"parameter name must be UPPER_SNAKE: {pname!r}")
        if _needs_default_under_auto(param, auto):
            errors.append(f"parameter {pname} is required but auto_promote fills no default")
        errors.extend(_bad_regex(pname, param))
        options = param.get("options")
        if options is not None and not isinstance(options, list):
            errors.append(f"parameter {pname}: options must be a list")
    return errors


def _unknown_param_keys(pname: str, param: dict) -> list[str]:
    unknown = set(param) - PARAM_KEYS
    if not unknown:
        return []
    return [f"parameter {pname}: unknown keys {sorted(unknown)}"]


def _needs_default_under_auto(param: dict, auto) -> bool:
    return bool(param.get("required")) and auto is not None and param.get("default_value") is None


def _bad_regex(pname: str, param: dict) -> list[str]:
    pattern = param.get("regex_pattern")
    if pattern is None:
        return []
    try:
        re.compile(str(pattern))
    except re.error as exc:
        return [f"parameter {pname}: regex_pattern does not compile: {exc}"]
    return []


def validate_promotion(entry: dict, root: Path) -> list[str]:
    """Every contract one promotion entry must satisfy."""
    errors: list[str] = []
    name = entry.get("name", "")
    if not name or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(name)):
        errors.append(f"promotion name must be kebab-case, got {name!r}")
    target = entry.get("pipeline_file", "")
    if not target:
        errors.append("promotion is missing pipeline_file")
    elif not (root / target).is_file():
        errors.append(f"pipeline_file does not exist: {target}")
    errors.extend(_validate_auto_promote(entry))
    errors.extend(_validate_parameters_block(entry))
    return errors


def validate_config(path: Path, root: Path) -> list[str]:
    """Every error across the config: structure, contracts, uniqueness."""
    entries = load_promotions(path)
    errors: list[str] = []
    seen: dict[str, int] = {}
    for entry in entries:
        name = str(entry.get("name", "?"))
        if name in seen:
            errors.append(f"duplicate promotion name: {name}")
        seen[name] = seen.get(name, 0) + 1
        errors.extend(validate_promotion(entry, root))
    return errors


def _declared_params(entry: dict) -> dict[str, dict]:
    params = entry.get("parameters") or {}
    env_vars = params.get("env_vars", []) if isinstance(params, dict) else []
    return {str(param.get("name")): param for param in env_vars}


def validate_parameters(entry: dict, supplied: dict[str, str]) -> list[str]:
    """Supplied parameters against one promotion's declared contract."""
    declared = _declared_params(entry)
    missing = sorted(
        name
        for name, param in declared.items()
        if param.get("required") and name not in supplied and param.get("default_value") is None
    )
    errors = [f"missing required parameter: {name}" for name in missing]
    for name, value in supplied.items():
        errors.extend(_one_supplied_param(declared, name, value))
    return errors


def _one_supplied_param(declared: dict[str, dict], name: str, value: str) -> list[str]:
    """One supplied value's contract errors against its declaration."""
    if name not in declared:
        return [f"undeclared parameter: {name}"]
    param = declared[name]
    errors: list[str] = []
    pattern = param.get("regex_pattern")
    if pattern is not None and not re.fullmatch(str(pattern), str(value)):
        errors.append(f"parameter {name}={value!r} fails regex {pattern!r}")
    options = param.get("options")
    if options is not None and str(value) not in {str(o) for o in options}:
        errors.append(f"parameter {name}={value!r} not in options {options}")
    return errors


def resolved_parameters(entry: dict, supplied: dict[str, str]) -> dict[str, str]:
    """Supplied values overlaid on defaults, as the promoted run sees them."""
    params = entry.get("parameters") or {}
    resolved: dict[str, str] = {}
    for param in params.get("env_vars", []) if isinstance(params, dict) else []:
        name = str(param.get("name"))
        if name in supplied:
            resolved[name] = str(supplied[name])
        elif param.get("default_value") is not None:
            resolved[name] = str(param["default_value"])
    return resolved


def dispatch_command(entry: dict, params: dict[str, str]) -> str:
    """The exact gh command the promotion would execute."""
    target = str(entry.get("pipeline_file"))
    ref = params.pop("GIT_REF", "main")
    parts = [f"gh workflow run {target}", f"--ref {ref}"]
    for key in sorted(params):
        parts.append(f"-f {key}={params[key]}")
    return " ".join(parts)


def find_promotion(entries: list[dict], name: str) -> dict:
    """The one named entry, or a usage error."""
    for entry in entries:
        if str(entry.get("name")) == name:
            return entry
    raise PromotionError(f"unknown promotion: {name} (known: {[e.get('name') for e in entries]})")


def _parse_params(pairs: list[str]) -> dict[str, str]:
    """k=v CLI pairs into the parameter dict."""
    out: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise PromotionError(f"parameter must be k=v, got {pair!r}")
        key, value = pair.split("=", 1)
        out[key] = value
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sem_promotions",
        description="Semaphore-style promotions: validate configs and promote",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_validate = sub.add_parser("validate", help="validate the promotions config")
    p_validate.add_argument("--config", default=DEFAULT_CONFIG)
    p_validate.add_argument("--root", default=".")
    p_validate.add_argument("--json", dest="as_json", action="store_true")
    p_promote = sub.add_parser("promote", help="validate params and print the dispatch")
    p_promote.add_argument("name")
    p_promote.add_argument("--config", default=DEFAULT_CONFIG)
    p_promote.add_argument("--root", default=".")
    p_promote.add_argument("-p", "--param", action="append", default=[], metavar="K=V")
    p_promote.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        if args.command == "validate":
            errors = validate_config(Path(args.config), root)
            if args.as_json:
                print(json.dumps({"valid": not errors, "errors": errors}))
            for err in errors:
                print(f"ERROR: {err}", file=sys.stderr)
            return 1 if errors else 0
        entry = find_promotion(load_promotions(Path(args.config)), args.name)
        supplied = _parse_params(args.param)
        errors = validate_parameters(entry, supplied)
        if errors:
            for err in errors:
                print(f"ERROR: {err}", file=sys.stderr)
            return 1
        params = resolved_parameters(entry, supplied)
        command = dispatch_command(dict(entry), dict(params))
        if args.as_json:
            print(json.dumps({"promotion": args.name, "parameters": params, "command": command}))
        else:
            print(command)
        return 0
    except PromotionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
