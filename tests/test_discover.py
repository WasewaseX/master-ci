"""Contract tests for workspace discovery (roadmap item 5).

Covers the documented contract: workspace markers enumerate their actual
members (npm/pnpm, go.work, Cargo), member globs expand to real manifest
directories, a declared member that cannot be backed by a manifest fails
closed (WorkspaceError -> exit 2, never root-only analysis), and plain
(non-workspace) manifest trees still inventory per directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import discover_projects as dp  # noqa: E402
import discover_workspaces as dw  # noqa: E402


def _make(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_npm_workspaces_enumerate_members(tmp_path: Path) -> None:
    _make(
        tmp_path,
        {
            "package.json": '{"name":"root","workspaces":["packages/*","apps/web"]}',
            "packages/alpha/package.json": '{"name":"@scope/alpha"}',
            "packages/beta/package.json": '{"name":"@scope/beta"}',
            "apps/web/package.json": '{"name":"web"}',
        },
    )
    inventory = dp.discover(tmp_path, "node")
    assert inventory["workspace"] is True
    paths = [p["path"] for p in inventory["projects"]]
    assert paths == [".", "apps/web", "packages/alpha", "packages/beta"]
    names = {p["path"]: p["name"] for p in inventory["projects"]}
    assert names["packages/alpha"] == "@scope/alpha"


def test_npm_workspaces_object_form(tmp_path: Path) -> None:
    _make(
        tmp_path,
        {
            "package.json": '{"workspaces":{"packages":["packages/*"]}}',
            "packages/one/package.json": '{"name":"one"}',
        },
    )
    paths = [p["path"] for p in dp.discover(tmp_path, "node")["projects"]]
    assert paths == [".", "packages/one"]


def test_pnpm_workspace_yaml(tmp_path: Path) -> None:
    _make(
        tmp_path,
        {
            "pnpm-workspace.yaml": "packages:\n  - pkgs/*\n",
            "package.json": "{}",
            "pkgs/a/package.json": '{"name":"a"}',
        },
    )
    paths = [p["path"] for p in dp.discover(tmp_path, "node")["projects"]]
    assert paths == [".", "pkgs/a"]


def test_pnpm_fallback_parser_matches_yaml() -> None:
    text = "packages:\n  - 'apps/*'\n  - pkgs/* # trailing comment\nother: x\n"
    assert dw._pnpm_fallback(text) == {"packages": ["apps/*", "pkgs/*"]}


def test_named_member_without_manifest_fails_closed(tmp_path: Path) -> None:
    _make(
        tmp_path,
        {
            "package.json": '{"workspaces":["packages/real","packages/ghost"]}',
            "packages/real/package.json": '{"name":"real"}',
        },
    )
    with pytest.raises(dw.WorkspaceError, match="packages/ghost"):
        dp.discover(tmp_path, "node")


def test_unmatched_glob_becomes_warning_not_error(tmp_path: Path) -> None:
    _make(
        tmp_path,
        {
            "package.json": '{"workspaces":["packages/*","features/*"]}',
            "packages/one/package.json": '{"name":"one"}',
        },
    )
    inventory = dp.discover(tmp_path, "node")
    assert inventory["warnings"] == ["workspace glob 'features/*' matched no member"]
    assert [p["path"] for p in inventory["projects"]] == [".", "packages/one"]


def test_malformed_pnpm_yaml_fails_closed(tmp_path: Path) -> None:
    _make(tmp_path, {"pnpm-workspace.yaml": "packages: [unclosed\n", "package.json": "{}"})
    with pytest.raises(dw.WorkspaceError, match="pnpm-workspace.yaml"):
        dp.discover(tmp_path, "node")


def test_pnpm_marker_without_root_package_json_fails_closed(tmp_path: Path) -> None:
    _make(tmp_path, {"pnpm-workspace.yaml": "packages:\n  - pkgs/*\n"})
    with pytest.raises(dw.WorkspaceError, match="package.json is missing"):
        dp.discover(tmp_path, "node")


def test_go_work_enumerates_modules(tmp_path: Path) -> None:
    _make(
        tmp_path,
        {
            "go.work": "go 1.22\n\nuse (\n\t.\n\t./mods/second\n)\n",
            "go.mod": "module example.com/root\n",
            "mods/second/go.mod": "module example.com/second\n",
        },
    )
    inventory = dp.discover(tmp_path, "go")
    assert inventory["workspace"] is True
    names = {p["path"]: p["name"] for p in inventory["projects"]}
    assert names == {".": "example.com/root", "mods/second": "example.com/second"}


def test_go_work_member_without_gomod_fails_closed(tmp_path: Path) -> None:
    _make(tmp_path, {"go.work": "use ./ghost\n", "go.mod": "module m\n"})
    with pytest.raises(dw.WorkspaceError, match="ghost"):
        dp.discover(tmp_path, "go")


def test_cargo_workspace_glob_and_exclude(tmp_path: Path) -> None:
    _make(
        tmp_path,
        {
            "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\nexclude = ["crates/three"]\n[package]\nname = "root"\n',
            "crates/one/Cargo.toml": '[package]\nname = "one"\n',
            "crates/two/Cargo.toml": '[package]\nname = "two"\n',
            "crates/three/Cargo.toml": '[package]\nname = "three"\n',
        },
    )
    inventory = dp.discover(tmp_path, "rust")
    names = {p["path"]: p["name"] for p in inventory["projects"]}
    assert names == {".": "root", "crates/one": "one", "crates/two": "two"}


def test_cargo_workspace_root_without_package_excluded(tmp_path: Path) -> None:
    _make(
        tmp_path,
        {
            "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
            "crates/one/Cargo.toml": '[package]\nname = "one"\n',
        },
    )
    paths = [p["path"] for p in dp.discover(tmp_path, "rust")["projects"]]
    assert paths == ["crates/one"]


def test_cargo_literal_member_missing_manifest_fails_closed(tmp_path: Path) -> None:
    _make(tmp_path, {"Cargo.toml": '[workspace]\nmembers = ["crates/ghost"]\n'})
    with pytest.raises(dw.WorkspaceError, match="crates/ghost"):
        dp.discover(tmp_path, "rust")


def test_malformed_cargo_toml_fails_closed(tmp_path: Path) -> None:
    # The workspace section IS declared, so the member list must be
    # readable; a broken manifest without any [workspace] marker is the
    # rust stack's failure to report, not discovery's.
    _make(tmp_path, {"Cargo.toml": "[workspace]\nmembers = ["})
    with pytest.raises(dw.WorkspaceError, match="Cargo.toml"):
        dp.discover(tmp_path, "rust")


def test_non_workspace_manifests_stay_per_directory(tmp_path: Path) -> None:
    _make(
        tmp_path,
        {
            "package.json": '{"name":"root"}',
            "service/api/package.json": '{"name":"api"}',
            "service/api/nested/node_modules/x/package.json": "{}",
        },
    )
    inventory = dp.discover(tmp_path, "node")
    assert inventory["workspace"] is False
    assert inventory["warnings"] == []
    assert [p["path"] for p in inventory["projects"]] == [".", "service/api"]


def test_discover_all_aggregates_families(tmp_path: Path) -> None:
    _make(tmp_path, {"package.json": '{"name":"root"}', "go.mod": "module m\n"})
    inventory = dp.discover_all(tmp_path)
    assert set(inventory["families"]) == set(dp.FAMILY_MANIFESTS) | {"dotnet"}
    assert [p["path"] for p in inventory["families"]["node"]["projects"]] == ["."]
    assert [p["path"] for p in inventory["families"]["go"]["projects"]] == ["."]


def test_cli_maps_workspace_error_to_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make(tmp_path, {"package.json": '{"workspaces":["packages/ghost"]}'})
    assert dp.main(["--family", "node", "--root", str(tmp_path), "--list"]) == 2
    assert "project discovery rejected" in capsys.readouterr().err


def test_cli_fail_empty_on_all_empty_families(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert dp.main(["--family", "all", "--root", str(tmp_path), "--fail-empty"]) == 1
    assert "no project manifest" in capsys.readouterr().err


def test_cli_all_fail_empty_passes_when_one_family_has_projects(tmp_path: Path) -> None:
    _make(tmp_path, {"package.json": '{"name":"root"}'})
    assert dp.main(["--family", "all", "--root", str(tmp_path), "--fail-empty"]) == 0


def test_summary_renders_names_and_warnings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make(
        tmp_path,
        {
            "package.json": '{"name":"root","workspaces":["packages/*","ghost/*"]}',
            "packages/a/package.json": '{"name":"a"}',
        },
    )
    assert dp.main(["--family", "node", "--root", str(tmp_path), "--format", "summary"]) == 0
    out = capsys.readouterr().out
    assert "- . (package.json: root)" in out
    assert "- packages/a (package.json: a)" in out
    assert "! workspace glob 'ghost/*' matched no member" in out
