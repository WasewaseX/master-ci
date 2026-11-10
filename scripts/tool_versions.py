#!/usr/bin/env python3
"""Emit pinned tool versions from ``configs/tool-versions.yml``.

Usage:
    python3 tool_versions.py pip ruff mypy lizard
        -> ruff==0.17.4 mypy==2.4.1 lizard==1.25.1
    python3 tool_versions.py npm biome depcheck
        -> biome@2.6.6 depcheck@1.4.7
    python3 tool_versions.py go golangci-lint
        -> v2.13.0  (bare refs: `go install pkg@$(...)`)
    python3 tool_versions.py cargo cargo-deny
        -> 0.20.2   (bare refs: `cargo install --version $(...)`)

Works without PyYAML: the pins file is a flat two-level mapping, parsed
by a minimal fallback when ``yaml`` is unavailable (fresh runners, slim
containers). An unpinned tool name is a hard error so a silent "latest"
can never sneak back in.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _load(path: Path) -> dict[str, dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return _load_flat(text)  # slim environments without PyYAML
    return yaml.safe_load(text) or {}


def _load_flat(text: str) -> dict[str, dict[str, str]]:
    """Minimal parser for the flat two-level pins file."""
    data: dict[str, dict[str, str]] = {}
    section: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        header = re.match(r"^(\w+):\s*$", line)
        if header:
            section = header.group(1)
            data[section] = {}
            continue
        entry = re.match(r"^\s+([^\s:]+):\s*([^\s#]+)", line)
        if entry and section:
            data[section][entry.group(1)] = entry.group(2).strip("'\"")
    return data


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: tool_versions.py <pip|npm|go|cargo> <tool>...", file=sys.stderr)
        return 2
    family, names = argv[0], argv[1:]
    if family not in ("pip", "npm", "go", "cargo"):
        print(f"unknown family: {family}", file=sys.stderr)
        return 2
    pins = _load(Path(__file__).resolve().parent.parent / "configs" / "tool-versions.yml")
    table = pins.get(family) or {}
    missing = [name for name in names if name not in table]
    if missing:
        print(
            f"tool(s) not pinned in configs/tool-versions.yml: {', '.join(missing)} - "
            "pin them instead of installing unpinned",
            file=sys.stderr,
        )
        return 1
    if family == "pip":
        print(" ".join(f"{name}=={table[name]}" for name in names))
    elif family in ("go", "cargo"):
        # go install / cargo install want a bare version ref; the pins
        # file stores exactly what the toolchain expects.
        print(" ".join(table[name] for name in names))
    else:
        print(" ".join(f"{name}@{table[name]}" for name in names))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
