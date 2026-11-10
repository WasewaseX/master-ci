"""Which CodeQL languages does this repository speak?

CodeQL Advanced (codeql.yml) defaults to ``languages: auto``: instead of
asking every caller to enumerate dialects, the detector reads the
manifests the repository already commits. Zero-config is the difference
between a documented capability and a used one - the same lesson the
audit took from Qodana and MegaLinter for the rest of the template.

Detection is a manifest sniff, deliberately shallow: CodeQL's own
auto-builder does the deep work later, and a stale manifest is still a
commit the maintainer wrote. When nothing supported is found the caller
asked for CodeQL and CodeQL cannot run - so the detector exits 2 with
the fix spelled out instead of quietly analyzing nothing. A gate that
cannot run fails, never skips.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Must stay in lockstep with the allowed set in codeql.yml's prepare job;
# test_workflow_contracts.py enforces the overlap so the two cannot drift.
SUPPORTED = (
    "c-cpp",
    "csharp",
    "go",
    "java-kotlin",
    "javascript-typescript",
    "python",
    "ruby",
    "rust",
    "swift",
)

# Manifests vote authoritatively (someone committed a build
# definition); extension probes catch manifest-less repositories.
MANIFESTS = {
    "go": ("go.mod", "go.sum"),
    "rust": ("Cargo.toml", "Cargo.lock"),
    "java-kotlin": (
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    ),
    "javascript-typescript": ("package.json",),
    "python": ("pyproject.toml", "setup.py", "requirements.txt"),
    "ruby": ("Gemfile",),
    "swift": ("Package.swift",),
    "c-cpp": ("CMakeLists.txt", "meson.build", "Makefile"),
}
EXTENSIONS = {
    ".go": "go",
    ".rs": "rust",
    ".kt": "java-kotlin",
    ".java": "java-kotlin",
    ".js": "javascript-typescript",
    ".mjs": "javascript-typescript",
    ".cjs": "javascript-typescript",
    ".jsx": "javascript-typescript",
    ".ts": "javascript-typescript",
    ".tsx": "javascript-typescript",
    ".py": "python",
    ".rb": "ruby",
    ".swift": "swift",
    ".cs": "csharp",
    ".c": "c-cpp",
    ".cc": "c-cpp",
    ".cpp": "c-cpp",
    ".cxx": "c-cpp",
    ".h": "c-cpp",
    ".hpp": "c-cpp",
}
# .csproj/.sln carry the csharp manifest hit; no dedicated extension map
# entry because the project files themselves are the signal.
CSHARP_SUFFIXES = (".csproj", ".sln", ".fsproj")

SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "target",
    "vendor",
    "coverage",
    "__pycache__",
    ".venv",
    "venv",
}


def classify(name: str) -> str | None:
    """The CodeQL language one committed file votes for, if any."""
    if name.endswith(CSHARP_SUFFIXES):
        return "csharp"
    for lang, manifests in MANIFESTS.items():
        if name in manifests:
            return lang
    return EXTENSIONS.get(Path(name).suffix.lower())


def detect_languages(root: Path) -> list[str]:
    """Return the sorted CodeQL languages ``root`` plausibly contains."""
    root = root.resolve()
    if not root.is_dir():
        raise SystemExit(f"::error::detection root {root} is not a directory")
    found: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            lang = classify(name)
            if lang:
                found.add(lang)
    return sorted(found)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        default=".",
        help="repository root to sniff (default: current directory)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "gh-output"),
        default="json",
        help="json prints the array; gh-output also writes languages= to GITHUB_OUTPUT",
    )
    args = parser.parse_args(argv)

    languages = detect_languages(Path(args.root))
    if not languages:
        print(
            "::error::languages: auto found no CodeQL-supportable code in this "
            "repository (no known manifest, no supported source file).",
            file=sys.stderr,
        )
        print(
            "::error::Either pass languages: explicitly (one of: "
            f"{', '.join(SUPPORTED)}) or remove the codeql.yml call - there "
            "is nothing for CodeQL to analyze.",
            file=sys.stderr,
        )
        return 2

    payload = json.dumps(languages)
    if args.format == "json":
        print(payload)
    else:
        print(f"languages={payload}")
        gh_output = os.environ.get("GITHUB_OUTPUT")
        if gh_output:
            with open(gh_output, "a", encoding="utf-8") as handle:
                handle.write(f"languages={payload}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
