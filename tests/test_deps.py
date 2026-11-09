"""Contract tests for the native dependency and deadness tranche.

Covers the v2.1 contracts documented in docs/checks.md:
- unused-dependency across the Go, Rust, PHP, C# and Java stacks, with
  the exemptions each oracle promises (indirect requires, dev tables,
  workspace catalogs, polyfills, platform constraints, build
  integration, test/provided scopes, dependencyManagement),
- monorepo scoping: a manifest is judged against the sources under its
  own directory,
- orphan-module: the stem oracle, the Go package-directory oracle, the
  entry/glue exemptions, and the config-file reference escape hatch.
"""

import importlib.util
import sys
from pathlib import Path

MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ai_slop_detector.py"
spec = importlib.util.spec_from_file_location("ai_slop_detector_deps", MOD_PATH)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

PHRASES = mod.load_phrases()


def scan(tmp_path: Path, files: dict[str, str]) -> list:
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return mod.scan(tmp_path, PHRASES)


def dep_hits(findings: list) -> list:
    return [f for f in findings if f.check == "unused-dependency"]


def orphan_hits(findings: list) -> list:
    return [f for f in findings if f.check == "orphan-module"]


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


def test_go_unused_direct_require_fires(tmp_path):
    findings = scan(
        tmp_path,
        {
            "go.mod": "module demo\n\ngo 1.23\n\nrequire (\n\tgolang.org/x/tools v0.1.0\n\tgithub.com/unused/thing v1.0.0\n)\n",
            "main.go": 'package main\n\nimport "golang.org/x/tools/go/analysis"\n\nvar _ = analysis.Analyzer{}\n',
        },
    )
    hits = dep_hits(findings)
    assert len(hits) == 1 and "unused/thing" in hits[0].message
    assert hits[0].path.endswith("go.mod")


def test_go_indirect_require_is_exempt(tmp_path):
    findings = scan(
        tmp_path,
        {
            "go.mod": "module demo\n\ngo 1.23\n\nrequire github.com/unused/thing v1.0.0 // indirect\n",
            "main.go": "package main\n\nfunc main() {}\n",
        },
    )
    assert not dep_hits(findings)


def test_go_path_prefix_counts_as_use(tmp_path):
    findings = scan(
        tmp_path,
        {
            "go.mod": "module demo\n\ngo 1.23\n\nrequire golang.org/x/tools v0.1.0\n",
            "main.go": 'package main\n\nimport "golang.org/x/tools/go/analysis"\n\nvar _ = analysis.Analyzer{}\n',
        },
    )
    assert not dep_hits(findings)


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------


def test_rust_unused_crate_fires_and_used_survives(tmp_path):
    findings = scan(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname = "demo"\n\n[dependencies]\nserde = "1"\nsleepy = "0.1"\n',
            "main.rs": "use serde::Serialize;\n\n#[derive(Serialize)]\nstruct X;\n",
        },
    )
    hits = dep_hits(findings)
    assert len(hits) == 1 and "sleepy" in hits[0].message
    assert hits[0].path.endswith("Cargo.toml")


def test_rust_underscore_spelling_counts_as_use(tmp_path):
    findings = scan(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname = "demo"\n\n[dependencies]\nserde-json = "1"\ntokio = "1"\n',
            "main.rs": "use serde_json::json;\n\nfn main() {\n    let _ = json!({});\n}\n",
        },
    )
    hits = dep_hits(findings)
    assert len(hits) == 1 and "tokio" in hits[0].message


def test_rust_dev_and_workspace_tables_exempt(tmp_path):
    findings = scan(
        tmp_path,
        {
            "Cargo.toml": '[workspace]\nmembers = ["crate"]\n\n[workspace.dependencies]\nserde = "1"\ncriterion = "0.5"\n',
            "crate/Cargo.toml": '[package]\nname = "demo"\n\n[dev-dependencies]\ncriterion = { workspace = true }\n',
            "crate/main.rs": "fn main() {}\n",
        },
    )
    assert not dep_hits(findings)


def test_rust_package_rename_use_counts(tmp_path):
    findings = scan(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname = "demo"\n\n[dependencies]\nlibxml = { package = "xml-five", version = "0.1" }\nsleepy = "0.1"\n',
            "main.rs": "use xml_five::parse;\n\nfn main() {\n    let _ = parse;\n}\n",
        },
    )
    hits = dep_hits(findings)
    assert len(hits) == 1 and "sleepy" in hits[0].message


# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------


def test_php_unused_require_fires_and_vendor_use_survives(tmp_path):
    findings = scan(
        tmp_path,
        {
            "composer.json": '{"require": {"guzzlehttp/guzzle": "^7.0", "monolog/monolog": "^3.0"}}\n',
            "src/Client.php": "<?php\n\nuse GuzzleHttp\\Client;\n\nfinal class Fetcher {\n  public function get(): Client { return new Client(); }\n}\n",
        },
    )
    hits = dep_hits(findings)
    assert len(hits) == 1 and "monolog" in hits[0].message


def test_php_dev_platform_and_polyfill_exempt(tmp_path):
    findings = scan(
        tmp_path,
        {
            "composer.json": '{"require": {"php": ">=8.1", "ext-json": "*", "symfony/polyfill-mbstring": "^1.0", "monolog/monolog": "^3.0"}, "require-dev": {"phpunit/phpunit": "^11"}}\n',
            "src/Log.php": "<?php\n\nfinal class Log {}\n",
        },
    )
    hits = dep_hits(findings)
    assert len(hits) == 1 and "monolog" in hits[0].message


# ---------------------------------------------------------------------------
# C#
# ---------------------------------------------------------------------------


def test_csharp_unused_package_fires_and_using_survives(tmp_path):
    findings = scan(
        tmp_path,
        {
            "App.csproj": '<Project Sdk="Microsoft.NET.Sdk">\n  <ItemGroup>\n    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />\n    <PackageReference Include="Serilog" Version="3.1.1" />\n  </ItemGroup>\n</Project>\n',
            "App.cs": "using Newtonsoft.Json;\n\npublic class App { public string ToJson() => JsonConvert.SerializeObject(this); }\n",
        },
    )
    hits = dep_hits(findings)
    assert len(hits) == 1 and "Serilog" in hits[0].message
    assert hits[0].path.endswith("App.csproj")


def test_csharp_build_integration_and_update_exempt(tmp_path):
    findings = scan(
        tmp_path,
        {
            "App.csproj": '<Project Sdk="Microsoft.NET.Sdk">\n  <ItemGroup>\n    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.11.1" />\n    <PackageReference Include="xunit.runner.visualstudio" Version="2.8.2" />\n    <PackageReference Include="Roslyn.Analyzers" Version="1.0" />\n    <PackageReference Update="Serilog" Version="3.1.1" />\n  </ItemGroup>\n</Project>\n',
            "App.cs": "public class App { }\n",
        },
    )
    assert not dep_hits(findings)


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------


def test_java_unused_compile_dep_fires_and_import_survives(tmp_path):
    pom = (
        '<?xml version="1.0"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
        "  <dependencies>\n"
        "    <dependency>\n      <groupId>org.apache.commons</groupId>\n"
        "      <artifactId>commons-lang3</artifactId>\n      <version>3.14.0</version>\n    </dependency>\n"
        "    <dependency>\n      <groupId>com.example</groupId>\n"
        "      <artifactId>my-weird-lib</artifactId>\n      <version>1.0</version>\n    </dependency>\n"
        "  </dependencies>\n</project>\n"
    )
    findings = scan(
        tmp_path,
        {
            "pom.xml": pom,
            "src/main/java/Demo.java": "import org.apache.commons.lang3.StringUtils;\n\npublic class Demo {\n  public String clean(String s) { return StringUtils.trim(s); }\n}\n",
        },
    )
    hits = dep_hits(findings)
    assert len(hits) == 1 and "my-weird-lib" in hits[0].message
    assert hits[0].path.endswith("pom.xml")


def test_java_test_scope_and_dependency_management_exempt(tmp_path):
    pom = (
        '<?xml version="1.0"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
        "  <dependencyManagement>\n    <dependencies>\n      <dependency>\n"
        "        <groupId>org.junit</groupId>\n        <artifactId>junit-bom</artifactId>\n"
        "        <version>5.10.2</version>\n      </dependency>\n    </dependencies>\n  </dependencyManagement>\n"
        "  <dependencies>\n    <dependency>\n      <groupId>org.junit.jupiter</groupId>\n"
        "      <artifactId>junit-jupiter</artifactId>\n      <scope>test</scope>\n    </dependency>\n"
        "  </dependencies>\n</project>\n"
    )
    findings = scan(
        tmp_path,
        {"pom.xml": pom, "src/main/java/Demo.java": "public class Demo { }\n"},
    )
    assert not dep_hits(findings)


# ---------------------------------------------------------------------------
# Monorepo scoping
# ---------------------------------------------------------------------------


def test_manifest_is_judged_against_its_own_tree(tmp_path):
    findings = scan(
        tmp_path,
        {
            "packages/a/package.json": '{"dependencies": {"left-pad": "1.0.0"}}\n',
            "packages/a/index.js": "import { pad } from 'left-pad';\n\nconsole.log(pad('x', 5));\n",
            "packages/b/package.json": '{"dependencies": {"left-pad": "1.0.0"}}\n',
            "packages/b/index.js": "console.log('nothing here');\n",
        },
    )
    hits = dep_hits(findings)
    assert len(hits) == 1
    assert hits[0].path == "packages/b/package.json"


def test_manifest_inside_ignored_tree_is_skipped(tmp_path):
    (tmp_path / ".ai-integrity-ignore").write_text("vendored/\n", encoding="utf-8")
    (tmp_path / "vendored").mkdir()
    (tmp_path / "vendored" / "package.json").write_text(
        '{"dependencies": {"left-pad": "1.0.0"}}', encoding="utf-8"
    )
    findings = mod.scan(tmp_path, PHRASES)
    assert not dep_hits(findings)


# ---------------------------------------------------------------------------
# orphan-module
# ---------------------------------------------------------------------------


def test_orphan_module_fires_on_unreferenced_file(tmp_path):
    findings = scan(
        tmp_path,
        {
            "src/legacy_utils.py": "def stale_helper():\n    return 1\n",
            "main.py": "from src import modern\n\nprint(modern.run())\n",
            "src/modern.py": "def run():\n    return 2\n",
        },
    )
    hits = orphan_hits(findings)
    assert len(hits) == 1 and "legacy_utils" in hits[0].message
    assert hits[0].advisory


def test_orphan_module_survives_config_reference(tmp_path):
    findings = scan(
        tmp_path,
        {
            "src/legacy_utils.py": "def stale_helper():\n    return 1\n",
            "main.py": "print('entry')\n",
            "docker-compose.yml": "services:\n  worker:\n    command: python -m src.legacy_utils\n",
        },
    )
    assert not orphan_hits(findings)


def test_orphan_module_exempts_tests_entries_and_glue(tmp_path):
    findings = scan(
        tmp_path,
        {
            "src/__init__.py": "",
            "src/mod.py": "VALUE = 1\n",
            "main.py": "from src.mod import VALUE\n\nprint(VALUE)\n",
            "tests/test_mod.py": "from src.mod import VALUE\n\ndef test_value():\n    assert VALUE == 1\n",
            "app.py": "print('boot')\n",
        },
    )
    assert not orphan_hits(findings)


def test_orphan_module_go_package_judged_by_directory(tmp_path):
    findings = scan(
        tmp_path,
        {
            "go.mod": "module demo\n\ngo 1.23\n",
            "main.go": "package main\n\nimport demo/internal/greeter\n\nfunc main() { greeter.Hi() }\n",
            "internal/greeter/greeter.go": "package greeter\n\nfunc Hi() {}\n",
            "internal/sleepy/sleepy.go": "package sleepy\n\nfunc Nap() {}\n",
        },
    )
    hits = orphan_hits(findings)
    assert len(hits) == 1
    assert "sleepy" in hits[0].message
    assert hits[0].path.endswith("sleepy.go")


def test_orphan_module_ruby_stem_survives_require_relative(tmp_path):
    findings = scan(
        tmp_path,
        {
            "lib/canary/greeter.rb": "module Canary\n  def self.greet\n    'hi'\n  end\nend\n",
            "spec/greeter_spec.rb": "require_relative '../lib/canary/greeter'\n",
        },
    )
    assert not orphan_hits(findings)
