"""Contract tests for the SBOM and license-policy tool (A19, round-19).

The documented contracts, test-pinned:

- the inventory: one package row per dependency declaration across the
    supported manifests, each with the version spec the manifest
    declares - verbatim (``NOASSERTION`` when it declares none, an
    exact pin collapses to its version, a range keeps its operators) -
    a purl built by each ecosystem's identity rules, and
    ``NOASSERTION`` for the license: a dependency's license is not the
    project's, so the project's MIT never reaches a dependency row;
- every declared dependency section is read - npm's four tables, PEP
    621 dependencies and optional-dependencies, PEP 735 groups, Poetry
    main/dev/groups, Cargo main/dev/build/target with ``package =``
    aliases - and ``requirements*.txt`` is one filename pattern;
- manifests are read by shape, not by spelling: PackageReference rows
    parse by element and attribute names, JSON shapes that do not fit
    are recorded gaps, one bad manifest never stops the scan;
- determinism: identical trees render byte-identical SPDX documents -
    sorted rows, digest namespace, constant creation timestamp;
- the policy: deny-list hits and deny_unknown gaps are violations,
    contradictions in the policy are configuration errors, and an
    unparseable manifest is a recorded data gap - never a silent skip;
- exit codes: 0 policy satisfied, 1 violations, 2 configuration error.
"""

import json
from pathlib import Path

import sem_sbom as sbom  # noqa: E402


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "requirements.txt").write_text(
        "requests==2.31.0\nflask>=3.0\n# comment\n-e .\n", encoding="utf-8"
    )
    _write(
        tmp_path / "package.json",
        json.dumps(
            {
                "name": "app",
                "license": "MIT",
                "dependencies": {"lodash": "^4.17.21", "@types/node": "20.0.0"},
            }
        ),
    )
    _write(
        tmp_path / "go.mod",
        "module example.com/m\n\ngo 1.21\n\nrequire (\n"
        "\tgithub.com/pkg/errors v0.9.1\n)\n\nrequire golang.org/x/text v0.14.0\n",
    )
    _write(
        tmp_path / "Cargo.toml",
        '[package]\nname = "app"\nlicense = "Apache-2.0"\n\n[dependencies]\nserde = "1.0"\n',
    )
    _write(
        tmp_path / "Gemfile", 'source "https://rubygems.org"\ngem "rails", "7.1.0"\ngem "puma"\n'
    )
    _write(
        tmp_path / "App.csproj",
        '<Project Sdk="Microsoft.NET.Sdk">\n  <ItemGroup>\n'
        '    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />\n'
        '    <PackageReference Include="Serilog" />\n'
        "  </ItemGroup>\n</Project>\n",
    )
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "app"\nlicense = { text = "MIT" }\n'
        'dependencies = ["httpx>=0.27"]\n\n[tool.poetry.dependencies]\npython = "^3.12"\nclick = "^8.1"\n',
    )
    return tmp_path


def _purls(result: sbom.SbomResult) -> set[str]:
    return {p.purl for p in result.packages}


def _versions(result: sbom.SbomResult) -> dict[str, str]:
    return {p.purl: p.version for p in result.packages}


def test_inventory_covers_all_six_ecosystems(tmp_path):
    _tree(tmp_path)
    result = sbom.scan_sbom(tmp_path)
    versions = _versions(result)
    assert versions["pkg:pypi/requests@2.31.0"] == "2.31.0"
    assert versions["pkg:pypi/flask"] == ">=3.0"
    assert versions["pkg:npm/lodash"] == "^4.17.21"
    assert versions["pkg:npm/%40types/node@20.0.0"] == "20.0.0"
    assert versions["pkg:golang/github.com/pkg/errors@v0.9.1"] == "v0.9.1"
    assert versions["pkg:golang/golang.org/x/text@v0.14.0"] == "v0.14.0"
    assert versions["pkg:cargo/serde@1.0"] == "1.0"
    assert versions["pkg:gem/rails@7.1.0"] == "7.1.0"
    assert versions["pkg:gem/puma"] == sbom.NOASSERTION
    assert versions["pkg:nuget/Newtonsoft.Json@13.0.3"] == "13.0.3"
    assert versions["pkg:nuget/Serilog"] == sbom.NOASSERTION
    assert versions["pkg:pypi/httpx"] == ">=0.27"
    assert versions["pkg:pypi/click"] == "^8.1"
    assert not result.gaps


def test_dependencies_never_inherit_the_project_license(tmp_path):
    """The manifest's license is the project's; deps stay NOASSERTION.

    An MIT application says nothing about its dependencies' licenses:
    stamping the project's license onto every dependency makes
    unknown-license dependencies pass ``deny_unknown`` and asserts MIT
    in the SPDX report on packages whose license nobody declared.
    """
    _tree(tmp_path)
    result = sbom.scan_sbom(tmp_path)
    assert result.packages, "the tree declares dependencies"
    assert all(p.license == sbom.NOASSERTION for p in result.packages)
    assert {row["manifest"]: row["license"] for row in result.project_licenses} == {
        "package.json": "MIT",
        "Cargo.toml": "Apache-2.0",
        "pyproject.toml": "MIT",
    }


def test_unknown_license_dependency_violates_under_deny_unknown(tmp_path):
    """The focused denial: MIT project, unknown dep, deny_unknown fires."""
    _write(
        tmp_path / "package.json",
        json.dumps({"license": "MIT", "dependencies": {"left-pad": "1.3.0"}}),
    )
    result = sbom.scan_sbom(tmp_path)
    policy = {"allow": ["MIT"], "deny": [], "deny_unknown": True}
    violations = sbom.judge_policy(result.packages, policy)
    assert [(v["package"], v["kind"]) for v in violations] == [
        ("pkg:npm/left-pad@1.3.0", "unknown-license")
    ]


def test_declared_ranges_keep_their_operators(tmp_path):
    """`demo!=1.0` is not `1.0`; `>=` stays distinguishable from `==`.

    Lstripping the comparison characters turns two opposite
    declarations into the same apparent pin, and Poetry's caret and
    tilde lose their meaning. The declared spec survives verbatim, and
    only an exact pin ever joins a purl as a version.
    """
    (tmp_path / "requirements.txt").write_text(
        "demo!=1.0\nthing>=1.0\npinned==2.0\nloose\nmulti>=1,<2\n", encoding="utf-8"
    )
    result = sbom.scan_sbom(tmp_path)
    versions = _versions(result)
    assert versions["pkg:pypi/demo"] == "!=1.0"
    assert versions["pkg:pypi/thing"] == ">=1.0"
    assert versions["pkg:pypi/pinned@2.0"] == "2.0"
    assert versions["pkg:pypi/loose"] == sbom.NOASSERTION
    assert versions["pkg:pypi/multi"] == ">=1,<2"
    assert not any(purl.startswith("pkg:pypi/demo@") for purl in _purls(result))


def test_extras_and_markers_are_parsed_separately(tmp_path):
    """Extras stay out of the name, markers stay out of the version."""
    (tmp_path / "requirements.txt").write_text(
        "requests[socks,use-chardet-on-py3]==2.31.0; python_version<'3.11'\n"
        "uvicorn[standard]>=0.27 # pinned for tls\n",
        encoding="utf-8",
    )
    result = sbom.scan_sbom(tmp_path)
    versions = _versions(result)
    assert versions["pkg:pypi/requests@2.31.0"] == "2.31.0"
    assert versions["pkg:pypi/uvicorn"] == ">=0.27"
    assert not any(purl.startswith("pkg:pypi/requests[") for purl in _purls(result))
    assert not any("python_version" in version for version in versions.values())


def test_requirements_glob_discovery_is_not_two_names(tmp_path):
    """`requirements-dev.txt` is inventory the same as `requirements.txt`.

    Discovery that accepts only two exact names silently drops the
    normal ``requirements-dev.txt`` / ``requirements-test.txt`` trees -
    neither parsed, neither reported as a gap.
    """
    for name in ("requirements.txt", "requirements-dev.txt", "requirements-test.txt"):
        _write(tmp_path / name, f"dep-for-{name}==1.0\n")
    result = sbom.scan_sbom(tmp_path)
    names = {p.name for p in result.packages}
    assert names == {
        "dep-for-requirements.txt",
        "dep-for-requirements-dev.txt",
        "dep-for-requirements-test.txt",
    }
    assert not result.gaps


def test_every_declared_dependency_section_reaches_the_inventory(tmp_path):
    """npm's four tables, PEP 621 optional, PEP 735 groups, Poetry groups.

    This repository's own pyproject declares its dependencies in
    ``[dependency-groups]`` - a reader that skips that section produces
    no rows and no gap, an empty inventory for a real manifest.
    """
    _write(
        tmp_path / "package.json",
        json.dumps(
            {
                "dependencies": {"prod-only": "1.0"},
                "devDependencies": {"dev-only": "2.0"},
                "optionalDependencies": {"opt-only": "3.0"},
                "peerDependencies": {"peer-only": "^4.0"},
            }
        ),
    )
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "app"\ndependencies = ["proj-dep>=1"]\n\n'
        '[project.optional-dependencies]\ntest = ["opt-dep==1.0"]\n\n'
        '[dependency-groups]\ndev = ["group-dep~=2.0"]\n\n'
        '[tool.poetry.dependencies]\npython = "^3.12"\npoetry-dep = "^8.1"\n\n'
        '[tool.poetry.group.lint.dependencies]\nruff-dep = "^0.4"\n',
    )
    _write(
        tmp_path / "Cargo.toml",
        '[package]\nname = "app"\n\n[dependencies]\nmain-dep = "1"\n\n'
        '[dev-dependencies]\ndev-dep = "2"\n\n[build-dependencies]\nbuild-dep = "3"\n\n'
        '[target."cfg(unix)".dependencies]\ntarget-dep = "4"\n',
    )
    names = {p.name for p in sbom.scan_sbom(tmp_path).packages}
    assert names == {
        "prod-only",
        "dev-only",
        "opt-only",
        "peer-only",
        "proj-dep",
        "opt-dep",
        "group-dep",
        "poetry-dep",
        "ruff-dep",
        "main-dep",
        "dev-dep",
        "build-dep",
        "target-dep",
    }


def test_cargo_alias_names_the_real_crate(tmp_path):
    """`serde1 = { package = "serde", ... }` inventories `serde`."""
    _write(
        tmp_path / "Cargo.toml",
        '[package]\nname = "app"\n\n[dependencies]\n'
        'serde1 = { package = "serde", version = "1" }\n'
        'json = { package = "serde_json", version = "1" }\n',
    )
    result = sbom.scan_sbom(tmp_path)
    assert _versions(result) == {
        "pkg:cargo/serde@1": "1",
        "pkg:cargo/serde_json@1": "1",
    }


def test_package_urls_preserve_ecosystem_identity(tmp_path):
    """npm scopes keep their escaped ``%40``; Go module paths keep case."""
    _write(tmp_path / "package.json", json.dumps({"dependencies": {"@types/node": "20"}}))
    _write(
        tmp_path / "go.mod",
        "module example.com/m\n\nrequire example.com/GoModule/Sized v1.0.0\n",
    )
    purls = _purls(sbom.scan_sbom(tmp_path))
    assert "pkg:npm/%40types/node@20" in purls
    assert "pkg:golang/example.com/GoModule/Sized@v1.0.0" in purls
    assert "pkg:golang/example.com/gomodule/sized" not in purls


def test_csproj_parses_by_attribute_and_child_names(tmp_path):
    """Attribute order, quoting, extra attributes and child Versions parse.

    A regex that demands Include first loses valid XML: reversed
    attributes, single quotes, a ``PrivateAssets`` attribute and a
    child ``<Version>`` element are all ordinary PackageReferences.
    """
    _write(
        tmp_path / "App.csproj",
        '<Project Sdk="Microsoft.NET.Sdk">\n  <ItemGroup>\n'
        '    <PackageReference Version="1.0" Include="Demo" />\n'
        "    <PackageReference Include='Quoted' Version='2.0' />\n"
        '    <PackageReference Include="WithExtras" Version="3.0" PrivateAssets="all" />\n'
        '    <PackageReference Include="ChildVersion">\n      <Version>4.0</Version>\n'
        "    </PackageReference>\n"
        "  </ItemGroup>\n</Project>\n",
    )
    result = sbom.scan_sbom(tmp_path)
    assert _versions(result) == {
        "pkg:nuget/Demo@1.0": "1.0",
        "pkg:nuget/Quoted@2.0": "2.0",
        "pkg:nuget/WithExtras@3.0": "3.0",
        "pkg:nuget/ChildVersion@4.0": "4.0",
    }


def test_malformed_manifest_shapes_are_recorded_gaps(tmp_path):
    """Wrong-shape JSON raises a caught parse error, not AttributeError.

    A valid JSON document of the wrong shape - a top-level list, a
    dependency table that is an array - must not stop the scan: the
    other manifests still parse, the bad one is a visible gap.
    """
    _write(tmp_path / "package.json", "[]")
    (tmp_path / "broken").mkdir()
    _write(tmp_path / "broken" / "package.json", json.dumps({"dependencies": ["demo"]}))
    _write(tmp_path / "requirements.txt", "requests==2.31.0\n")
    result = sbom.scan_sbom(tmp_path)
    assert [p.purl for p in result.packages] == ["pkg:pypi/requests@2.31.0"]
    assert {gap["manifest"] for gap in result.gaps} == {"package.json", "broken/package.json"}
    assert all(gap["reason"] for gap in result.gaps)


def test_non_xml_csproj_is_a_gap(tmp_path):
    _write(tmp_path / "App.csproj", "<this is not xml")
    _write(tmp_path / "requirements.txt", "requests==2.31.0\n")
    result = sbom.scan_sbom(tmp_path)
    assert [p.purl for p in result.packages] == ["pkg:pypi/requests@2.31.0"]
    assert len(result.gaps) == 1
    assert "XML" in result.gaps[0]["reason"] or "xml" in result.gaps[0]["reason"]


def test_unparseable_manifest_is_a_recorded_gap(tmp_path):
    _write(tmp_path / "package.json", "{not json")
    _write(tmp_path / "requirements.txt", "requests==2.31.0\n")
    result = sbom.scan_sbom(tmp_path)
    assert [p.purl for p in result.packages] == ["pkg:pypi/requests@2.31.0"]
    assert len(result.gaps) == 1
    assert result.gaps[0]["manifest"] == "package.json"
    assert result.gaps[0]["reason"]


def test_poetry_specs_keep_their_caret_and_tilde(tmp_path):
    _write(
        tmp_path / "pyproject.toml",
        '[tool.poetry.dependencies]\npython = "^3.12"\nclick = "^8.1"\ntomli = { version = ">=2.0", optional = true }\nurl-dep = { git = "https://example.com/x" }\n',
    )
    result = sbom.scan_sbom(tmp_path)
    assert _versions(result) == {
        "pkg:pypi/click": "^8.1",
        "pkg:pypi/tomli": ">=2.0",
        "pkg:pypi/url-dep": sbom.NOASSERTION,
    }


def test_sbom_document_is_deterministic_and_spdx_shaped(tmp_path):
    _tree(tmp_path)
    first = sbom.build_spdx(sbom.scan_sbom(tmp_path))
    second = sbom.build_spdx(sbom.scan_sbom(tmp_path))
    assert first == second
    assert first["spdxVersion"] == "SPDX-2.3"
    assert first["dataLicense"] == "CC0-1.0"
    assert first["SPDXID"] == "SPDXRef-DOCUMENT"
    digest = Path(first["name"]).name.split("master-ci-sbom-")[-1]
    assert first["documentNamespace"].endswith(digest)
    purls = [ref["referenceLocator"] for p in first["packages"] for ref in p["externalRefs"]]
    assert purls == sorted(purls)


def test_the_project_license_stays_out_of_the_policy_judgment(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "app"\nlicense = "Apache-2.0"\ndependencies = ["httpx>=0.27"]\n',
    )
    result = sbom.scan_sbom(tmp_path)
    assert result.project_licenses == [{"manifest": "pyproject.toml", "license": "Apache-2.0"}]
    # The project's own license stays with the project report; the
    # dependency rows carry no license, so only per-dep evidence can
    # ever deny or allow them.
    policy = {"allow": [], "deny": ["Apache-2.0"], "deny_unknown": False}
    assert sbom.judge_policy(result.packages, policy) == []


def test_deny_unknown_makes_missing_licenses_violations(tmp_path):
    _tree(tmp_path)
    result = sbom.scan_sbom(tmp_path)
    policy = {"allow": ["MIT", "Apache-2.0"], "deny": [], "deny_unknown": True}
    violations = sbom.judge_policy(result.packages, policy)
    unknown = [v for v in violations if v["kind"] == "unknown-license"]
    assert unknown, "every dependency is undeclared-license under the honest rule"
    assert all(v["license"] == sbom.NOASSERTION for v in unknown)
    assert not sbom.judge_policy(result.packages, {"allow": [], "deny": [], "deny_unknown": False})


def test_contradictory_policy_is_a_configuration_error(tmp_path):
    policy_path = _write(
        tmp_path / "policy.json",
        json.dumps({"allow": ["MIT"], "deny": ["MIT"], "deny_unknown": False}),
    )
    try:
        sbom.load_policy(policy_path)
    except ValueError as exc:
        assert "contradicts" in str(exc)
    else:
        raise AssertionError("an allow==deny overlap must refuse")


def test_cli_exit_codes(tmp_path, capsys):
    _tree(tmp_path)
    policy = _write(
        tmp_path / "policy.json",
        json.dumps({"allow": [], "deny": [], "deny_unknown": False}),
    )
    bad_policy = _write(tmp_path / "bad.json", json.dumps(["not", "a", "policy"]))
    sbom_path = tmp_path / "sbom.json"
    assert sbom.main(["--scan", str(tmp_path), "--sbom", str(sbom_path)]) == 0
    assert json.loads(sbom_path.read_text())["spdxVersion"] == "SPDX-2.3"
    assert sbom.main(["--scan", str(tmp_path), "--policy", str(policy)]) == 0
    strict = _write(
        tmp_path / "strict.json",
        json.dumps({"allow": [], "deny": [], "deny_unknown": True}),
    )
    assert sbom.main(["--scan", str(tmp_path), "--policy", str(strict)]) == 1
    assert sbom.main(["--scan", str(tmp_path), "--policy", str(bad_policy)]) == 2
    assert sbom.main(["--scan", str(tmp_path / "missing")]) == 2
    out = capsys.readouterr().out
    assert "License policy: violations" in out
    assert "Project licenses" in out


def test_report_json_carries_project_licenses_gaps_and_violations(tmp_path):
    _tree(tmp_path)
    (tmp_path / "broken").mkdir()
    _write(tmp_path / "broken" / "package.json", "{oops")
    report_path = tmp_path / "report.json"
    strict = _write(
        tmp_path / "strict.json",
        json.dumps({"allow": [], "deny": [], "deny_unknown": True}),
    )
    assert (
        sbom.main(["--scan", str(tmp_path), "--policy", str(strict), "--report", str(report_path)])
        == 1
    )
    report = json.loads(report_path.read_text())
    assert report["schema"] == "sbom-report-v1"
    assert report["violations"] and report["gaps"]
    assert any(gap["manifest"].startswith("broken") for gap in report["gaps"])
    assert {row["manifest"]: row["license"] for row in report["project_licenses"]} == {
        "package.json": "MIT",
        "Cargo.toml": "Apache-2.0",
        "pyproject.toml": "MIT",
    }
