"""The Semaphore compile-time secret contract, enforced statically.

    Semaphore fails a pipeline at compile time when it imports a secret
    name that does not exist. GitHub Actions fails at runtime instead -
    usually as a silent empty string, which is worse: the job runs and
    "passes" without the credential. This lint ports the compile-time
    contract to workflow YAML:

    - every `${{ secrets.NAME }}` used under .github/workflows/ must be
        declared in docs/secrets-manifest.yml - an unknown name exits 1,
    - a workflow triggered by `pull_request` that consumes secrets gets
        flagged: forked-PR runs receive empty secrets unless explicitly
        allow-listed, so the consumption is either dead or a leak path,
    - raw `${{ secrets.NAME }}` interpolation directly inside an
        `echo`/`printf` command line gets flagged: it lands in logs
        unmasked-adjacent and the shell history of the runner.

    Exit codes: 0 clean, 1 violation, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

WORKFLOW_DIR = ".github/workflows"
MANIFEST = "docs/secrets-manifest.yml"
# The expression may continue after the name - `secrets.X || github.token`
# is the common fallback form - so anything without a closing brace is
# expression tail, and the name is the capture.
_SECRET_USE = re.compile(r"\$\{\{\s*secrets\.([A-Za-z_][A-Za-z0-9_]*)[^}]*\}\}")
_ECHO_LINE = re.compile(r"^\s*(?:- )?(?:run:\s*)?(?:echo|printf)\s.*\$\{\{\s*secrets\.")
MANIFEST_KEYS = frozenset({"description", "used_by", "url"})


class ManifestError(ValueError):
    """The manifest itself is broken."""


def load_manifest(path: Path) -> dict[str, dict]:
    """Declared secrets by name, with shape validation."""
    import yaml

    if not path.is_file():
        raise ManifestError(f"secrets manifest not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    secrets = payload.get("secrets")
    if not isinstance(secrets, list) or not secrets:
        raise ManifestError("manifest must carry a non-empty 'secrets' list")
    declared: dict[str, dict] = {}
    for entry in secrets:
        name = entry.get("name", "")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", str(name)):
            raise ManifestError(f"secret name must be UPPER_SNAKE: {name!r}")
        unknown = set(entry) - MANIFEST_KEYS - {"name"}
        if unknown:
            raise ManifestError(f"secret {name}: unknown manifest keys {sorted(unknown)}")
        declared[str(name)] = entry
    return declared


def workflow_files(root: Path) -> list[Path]:
    """Every workflow YAML in the repo."""
    workflows = root / WORKFLOW_DIR
    if not workflows.is_dir():
        return []
    return sorted(p for p in workflows.iterdir() if p.suffix in (".yml", ".yaml"))


def triggers_on_pull_request(text: str) -> bool:
    """True when the workflow's `on:` block includes pull_request."""
    match = re.search(r"(?m)^on:\n((?:\s{2,}.*\n)+)", text)
    return bool(match and "pull_request" in match.group(1))


def scan(root: Path, manifest_path: Path) -> tuple[list[str], list[str]]:
    """(errors, warnings) across every workflow file.

    Errors are the compile-time contract (unknown names) and the fork
    exposure; warnings are the log-interpolation hygiene nits.
    """
    declared = load_manifest(manifest_path)
    # GitHub secret names are case-insensitive at runtime: the repo's
    # lowercase `secrets.dockerhub_token` and a declared DOCKERHUB_TOKEN
    # are the same credential. Matching is case-insensitive; a lowercase
    # spelling is a convention warning, not a compile error.
    declared_ci: dict[str, dict] = {
        name.upper(): entry for name, entry in declared.items()
    }
    errors: list[str] = []
    warnings: list[str] = []
    used_names: set[str] = set()
    for path in workflow_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        used = set(_SECRET_USE.findall(text))
        used_names |= used
        unknown = {name for name in used if name.upper() not in declared_ci}
        for name in sorted(unknown):
            errors.append(
                f"{rel}: secret '{name}' is not declared in {manifest_path} - "
                "GitHub passes an empty string at runtime; declare it or fix the name"
            )
        for name in sorted(
            name for name in used if name not in declared and name.upper() in declared_ci
        ):
            warnings.append(
                f"{rel}: secret '{name}' spelled lowercase - use the "
                "declared UPPER_SNAKE form in workflow YAML"
            )
        if used and triggers_on_pull_request(text):
            for trigger in ("pull_request_target", "pull_request"):
                if trigger in text and not _has_fork_guard(text):
                    errors.append(
                        f"{rel}: consumes secrets {sorted(used)} on a "
                        f"{trigger} trigger without a fork guard - forked "
                        "PRs receive empty secrets or, with "
                        "pull_request_target, an exfiltration path"
                    )
                    break
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _ECHO_LINE.search(line):
                warnings.append(f"{rel}:{lineno}: secret interpolated into an echo/printf line")
    for name in sorted(
        name for name in declared if not any(name.upper() == u.upper() for u in used_names)
    ):
        warnings.append(
            f"{name} is declared in {manifest_path} but no workflow uses it - "
            "retire the credential or document the consumer"
        )
    return errors, warnings


def _has_fork_guard(text: str) -> bool:
    """An explicit fork gate: the checkout of the base repo, a condition,
    or an approval action the maintainers control."""
    lowered = text.lower()
    return (
        "github.event.pull_request.head.repo.fork" in text
        or "github.event_name == 'pull_request_target'" in text
        and "head" not in lowered
        or "zizmor: ignore" in lowered
        and "pull_request_target" in lowered
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sem_secrets_lint",
        description="compile-time secret-name contract for workflow YAML",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--manifest", default=MANIFEST)
    parser.add_argument("--json", dest="as_json", action="store_true")
    parser.add_argument("--fail-on-warning", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        errors, warnings = scan(root, Path(args.manifest))
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps({"errors": errors, "warnings": warnings}))
    for item in warnings:
        print(f"WARN: {item}", file=sys.stderr)
    for item in errors:
        print(f"ERROR: {item}", file=sys.stderr)
    if errors:
        return 1
    return 1 if args.fail_on_warning and warnings else 0


if __name__ == "__main__":
    sys.exit(main())
