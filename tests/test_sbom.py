"""Contract tests for the SBOM and license-policy tool (A19, round-19).

The documented contracts, test-pinned:

- the inventory: one package row per dependency declaration across the
    six supported ecosystems, each with the version the manifest
    declares (NOASSERTION when it does not), a purl, and the license
    the manifest itself declares;
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


def test_inventory_covers_all_six_ecosystems(tmp_path):
    _tree(tmp_path)
    result = sbom.scan_sbom(tmp_path)
    purls = _purls(result)
    assert "pkg:pypi/requests@2.31.0" in purls
    assert "pkg:pypi/flask@3.0" in purls
    assert "pkg:npm/lodash@^4.17.21" in purls
    assert "pkg:npm/types/node@20.0.0" in purls
    assert "pkg:golang/github.com/pkg/errors@v0.9.1" in purls
    assert "pkg:golang/golang.org/x/text@v0.14.0" in purls
    assert "pkg:cargo/serde@1.0" in purls
    assert "pkg:gem/rails@7.1.0" in purls
    assert "pkg:gem/puma" in purls
    assert "pkg:nuget/Newtonsoft.Json@13.0.3" in purls
    assert "pkg:nuget/Serilog" in purls
    assert not result.gaps


def test_declared_license_rides_on_the_manifests_packages(tmp_path):
    _tree(tmp_path)
    result = sbom.scan_sbom(tmp_path)
    licenses = {p.purl: p.license for p in result.packages}
    assert licenses["pkg:npm/lodash@^4.17.21"] == "MIT"
    assert licenses["pkg:cargo/serde@1.0"] == "Apache-2.0"
    assert licenses["pkg:pypi/httpx@0.27"] == "MIT"
    assert licenses["pkg:pypi/requests@2.31.0"] == sbom.NOASSERTION
    assert licenses["pkg:gem/rails@7.1.0"] == sbom.NOASSERTION


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


def test_denied_license_is_a_violation(tmp_path):
    _tree(tmp_path)
    result = sbom.scan_sbom(tmp_path)
    policy = {"allow": ["MIT"], "deny": ["Apache-2.0"], "deny_unknown": False}
    kinds = {(v["package"], v["kind"]) for v in sbom.judge_policy(result.packages, policy)}
    assert ("pkg:cargo/serde@1.0", "denied-license") in kinds
    assert all(kind == "denied-license" for _, kind in kinds)


def test_deny_unknown_makes_missing_licenses_violations(tmp_path):
    _tree(tmp_path)
    result = sbom.scan_sbom(tmp_path)
    policy = {"allow": ["MIT", "Apache-2.0"], "deny": [], "deny_unknown": True}
    violations = sbom.judge_policy(result.packages, policy)
    unknown = [v for v in violations if v["kind"] == "unknown-license"]
    assert unknown, "the unpinned manifests must surface as unknown-license rows"
    assert all(v["license"] == sbom.NOASSERTION for v in unknown)
    assert not sbom.judge_policy(
        result.packages, {"allow": ["MIT"], "deny": [], "deny_unknown": False}
    )


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


def test_unparseable_manifest_is_a_recorded_gap(tmp_path):
    _write(tmp_path / "package.json", "{not json")
    _write(tmp_path / "requirements.txt", "requests==2.31.0\n")
    result = sbom.scan_sbom(tmp_path)
    assert [p.purl for p in result.packages] == ["pkg:pypi/requests@2.31.0"]
    assert len(result.gaps) == 1
    assert result.gaps[0]["manifest"] == "package.json"
    assert result.gaps[0]["reason"]


def test_scoped_npm_name_uses_the_purl_namespace(tmp_path):
    _write(tmp_path / "package.json", json.dumps({"dependencies": {"@types/node": "20"}}))
    result = sbom.scan_sbom(tmp_path)
    assert result.packages[0].purl == "pkg:npm/types/node@20"


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
        json.dumps({"allow": [], "deny": ["MIT"], "deny_unknown": True}),
    )
    assert sbom.main(["--scan", str(tmp_path), "--policy", str(strict)]) == 1
    assert sbom.main(["--scan", str(tmp_path), "--policy", str(bad_policy)]) == 2
    assert sbom.main(["--scan", str(tmp_path / "missing")]) == 2
    out = capsys.readouterr().out
    assert "License policy: violations" in out


def test_report_json_carries_gaps_and_violations(tmp_path):
    _tree(tmp_path)
    (tmp_path / "broken").mkdir()
    _write(tmp_path / "broken" / "package.json", "{oops")
    report_path = tmp_path / "report.json"
    strict = _write(
        tmp_path / "strict.json",
        json.dumps({"allow": [], "deny": ["MIT"], "deny_unknown": True}),
    )
    assert (
        sbom.main(["--scan", str(tmp_path), "--policy", str(strict), "--report", str(report_path)])
        == 1
    )
    report = json.loads(report_path.read_text())
    assert report["schema"] == "sbom-report-v1"
    assert report["violations"] and report["gaps"]
    assert any(gap["manifest"].startswith("broken") for gap in report["gaps"])
