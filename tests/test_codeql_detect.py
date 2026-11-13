"""The CodeQL language detector: manifest sniffing, fail-closed on none.

The detector is the zero-config entry into CodeQL Advanced - these tests
pin the manifest map, the skip rules, the sorted deterministic output and
the exit-2 contract when a repository offers CodeQL nothing to analyze.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from codeql_detect import SUPPORTED, detect_languages, main  # noqa: E402


def make_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def test_manifest_map_covers_every_supported_language(tmp_path: Path) -> None:
    languages = {
        "go",
        "rust",
        "java-kotlin",
        "javascript-typescript",
        "python",
        "ruby",
        "swift",
        "c-cpp",
    }
    manifests = [
        m
        for lang in languages
        for m in dict.fromkeys(
            {
                "go": ("go.mod",),
                "rust": ("Cargo.toml",),
                "java-kotlin": ("pom.xml",),
                "javascript-typescript": ("package.json",),
                "python": ("pyproject.toml",),
                "ruby": ("Gemfile",),
                "swift": ("Package.swift",),
                "c-cpp": ("CMakeLists.txt",),
            }[lang]
        )
    ]
    for manifest in manifests:
        make_tree(tmp_path, {manifest: "# probe"})
        assert detect_languages(tmp_path) == sorted(
            lang
            for lang, mans in (
                ("go", ("go.mod",)),
                ("rust", ("Cargo.toml",)),
                ("java-kotlin", ("pom.xml",)),
                ("javascript-typescript", ("package.json",)),
                ("python", ("pyproject.toml",)),
                ("ruby", ("Gemfile",)),
                ("swift", ("Package.swift",)),
                ("c-cpp", ("CMakeLists.txt",)),
            )
            if manifest in mans
        ), manifest
        (tmp_path / manifest).unlink()


def test_empty_repository_exits_two_with_guidance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_tree(tmp_path, {"README.md": "# nothing analyzable"})
    assert main(["--root", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "::error::" in err
    assert "languages:" in err
    assert ", ".join(SUPPORTED) in err


def test_exit_two_is_not_a_json_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_tree(tmp_path, {"docs/architecture.md": "text"})
    assert main(["--root", str(tmp_path), "--format", "json"]) == 2
    assert capsys.readouterr().out.strip() == ""


def test_go_and_node_monorepo(tmp_path: Path) -> None:
    make_tree(
        tmp_path,
        {
            "go.mod": "module example.com/m",
            "services/api/main.go": "package main",
            "web/package.json": '{"name": "web"}',
            "web/app.ts": "export const x = 1;",
        },
    )
    assert detect_languages(tmp_path) == ["go", "javascript-typescript"]


def test_nested_jvm_and_rust_workspaces(tmp_path: Path) -> None:
    make_tree(
        tmp_path,
        {
            "server/build.gradle.kts": "// jvm",
            "server/src/main/kotlin/App.kt": "class App",
            "crates/core/Cargo.toml": "[package]",
            "crates/core/src/lib.rs": "pub fn f() {}",
        },
    )
    assert detect_languages(tmp_path) == ["java-kotlin", "rust"]


def test_python_by_extension_without_manifest(tmp_path: Path) -> None:
    make_tree(tmp_path, {"scripts/tool.py": "print('hi')"})
    assert detect_languages(tmp_path) == ["python"]


def test_csharp_project_files_and_c_fallback(tmp_path: Path) -> None:
    make_tree(
        tmp_path,
        {
            "src/App/App.csproj": "<Project />",
            "native/lib/CMakeLists.txt": "cmake_minimum_required()",
        },
    )
    assert detect_languages(tmp_path) == ["c-cpp", "csharp"]


def test_skip_dirs_keep_vendored_code_out(tmp_path: Path) -> None:
    make_tree(
        tmp_path,
        {
            "vendor/github.com/x/y/go.mod": "module other",
            "node_modules/left-pad/package.json": "{}",
            "target/debug/main.rs": "fn main() {}",
            "dist/bundle.js": "",
            "coverage/lcov.info": "",
        },
    )
    assert detect_languages(tmp_path) == []


def test_output_is_sorted_and_stable(tmp_path: Path) -> None:
    make_tree(
        tmp_path,
        {
            "z/Cargo.toml": "[package]",
            "a/go.mod": "module m",
            "m/package.json": "{}",
            "p/app.py": "",
        },
    )
    first = detect_languages(tmp_path)
    second = detect_languages(tmp_path)
    assert first == ["go", "javascript-typescript", "python", "rust"]
    assert first == second


def test_gh_output_writes_step_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out_file = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    make_tree(tmp_path, {"main.py": "", "pyproject.toml": ""})
    assert main(["--root", str(tmp_path), "--format", "gh-output"]) == 0
    assert f"languages={json.dumps(['python'])}" in out_file.read_text()
    assert f"languages={json.dumps(['python'])}" in capsys.readouterr().out


def test_gh_output_without_env_still_prints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    make_tree(tmp_path, {"go.mod": "module m"})
    assert main(["--root", str(tmp_path), "--format", "gh-output"]) == 0
    assert "languages=" in capsys.readouterr().out
