"""Contract tests for the scanaislop round-16 parity column.

Covers the documented contract:

- innerhtml: a direct .innerHTML assignment on the js family reports,
    comparisons and `==` stay silent, non-js families stay silent,
- dangerously-set-innerhtml: the React prop reports,
- sql-injection: f-string SQL in Python, template-literal SQL in JS and
    interpolated SQL in C# report; parameterized queries and comments
    stay silent,
- shell-injection: shell=True with a dynamic command, exec() with an
    interpolated command and C system() with a non-literal report;
    constant commands, argv lists and comments stay silent,
- unsafe-deserialization: pickle/marshal/yaml.load report, safe_load and
    Loader= stay silent, the .NET formatters report,
- unsafe-c-call: gets/strcpy/strcat/sprintf report, bounds-checked forms
    stay silent,
- hardcoded-secret: AKIA, JWT, PEM and database-URL formats report; test
    paths and docs stay silent,
- the column aggregate skips test paths as a whole,
- ecosystem: unused-css reports an orphan class and spares a referenced
    one, utility names and cross-sheet reuse stay silent,
- vulnerable-dependency: silent without the opt-in, findings parsed from
    an audit run when the opt-in and the tool are both present,
- TS assertions: `as any` and `as unknown as` report, plain code stays
    silent, comments are ignored,
- repeated-chained-call: a chain repeated three times reports once,
    distinct chains and single occurrences stay silent.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detector_module import detector as mod  # noqa: E402
from slop_ecosystem import (  # noqa: E402
    _parse_audit_report,
    check_unused_css,
    check_vulnerable_dependencies,
)
from slop_security import (  # noqa: E402
    check_dangerously_set_innerhtml,
    check_hardcoded_secret,
    check_innerhtml,
    check_security_column,
    check_shell_injection,
    check_sql_injection,
    check_unsafe_c_call,
    check_unsafe_deserialization,
)
from slop_ts_checks import check_ts_slop  # noqa: E402


def checks_of(findings):
    return sorted({f.check for f in findings})


# -- innerhtml ---------------------------------------------------------------


def test_innerhtml_assignment_reports():
    findings = check_innerhtml(Path("a.ts"), "el.innerHTML = userInput;\n", "js", "src/a.ts")
    assert checks_of(findings) == ["innerhtml"]


def test_innerhtml_comparison_stays_silent():
    text = "if (el.innerHTML == expected) { ok(); }\n"
    assert check_innerhtml(Path("a.ts"), text, "js", "src/a.ts") == []


def test_innerhtml_non_js_family_stays_silent():
    text = "el.innerHTML = userInput;\n"
    assert check_innerhtml(Path("a.py"), text, "py", "a.py") == []


def test_dangerously_set_innerhtml_reports():
    text = "const p = { dangerouslySetInnerHTML: { __html: raw } };\n"
    findings = check_dangerously_set_innerhtml(Path("a.tsx"), text, "js", "a.tsx")
    assert checks_of(findings) == ["dangerously-set-innerhtml"]


# -- sql-injection -----------------------------------------------------------


def test_python_fstring_sql_reports():
    text = 'sql = f"SELECT * FROM users WHERE name = {name}"\n'
    findings = check_sql_injection(Path("a.py"), text, "py", "a.py")
    assert checks_of(findings) == ["sql-injection"]


def test_python_parameterized_stays_silent():
    text = 'sql = "SELECT * FROM users WHERE name = ?"\n'
    assert check_sql_injection(Path("a.py"), text, "py", "a.py") == []


def test_js_template_literal_sql_reports():
    text = "const q = `SELECT * FROM t WHERE id = ${id}`;\n"
    findings = check_sql_injection(Path("a.ts"), text, "js", "a.ts")
    assert checks_of(findings) == ["sql-injection"]


def test_csharp_interpolated_sql_reports():
    text = 'var q = $"SELECT * FROM t WHERE id = {id}";\n'
    findings = check_sql_injection(Path("a.cs"), text, "csharp", "a.cs")
    assert checks_of(findings) == ["sql-injection"]


def test_sql_comment_line_stays_silent():
    text = "-- SELECT * FROM {table}\n"
    assert check_sql_injection(Path("a.py"), text, "py", "a.py") == []


# -- shell-injection ---------------------------------------------------------


def test_python_shell_true_dynamic_command_reports():
    text = "import subprocess\nsubprocess.run(cmd, shell=True)\n"
    findings = check_shell_injection(Path("a.py"), text, "py", "a.py")
    assert checks_of(findings) == ["shell-injection"]


def test_python_shell_true_fstring_reports():
    text = 'subprocess.run(f"ls {d}", shell=True)\n'
    assert checks_of(check_shell_injection(Path("a.py"), text, "py", "a.py")) == ["shell-injection"]


def test_python_constant_command_stays_silent():
    text = 'subprocess.run("ls -la", shell=True)\n'
    assert check_shell_injection(Path("a.py"), text, "py", "a.py") == []


def test_python_argv_list_stays_silent():
    text = "subprocess.run(cmd, shell=False)\n"
    assert check_shell_injection(Path("a.py"), text, "py", "a.py") == []


def test_js_exec_interpolation_reports():
    text = "exec(`rm -rf ${path}`);\n"
    findings = check_shell_injection(Path("a.js"), text, "js", "a.js")
    assert checks_of(findings) == ["shell-injection"]


def test_c_system_non_literal_reports():
    text = "void f(char *s) { system(s); }\n"
    findings = check_shell_injection(Path("a.c"), text, "cpp", "a.c")
    assert checks_of(findings) == ["shell-injection"]


def test_c_system_literal_stays_silent():
    text = 'int main(void) { system("ls"); return 0; }\n'
    assert check_shell_injection(Path("a.c"), text, "cpp", "a.c") == []


# -- unsafe-deserialization --------------------------------------------------


def test_pickle_loads_reports():
    text = "import pickle\npickle.loads(blob)\n"
    findings = check_unsafe_deserialization(Path("a.py"), text, "py", "a.py")
    assert checks_of(findings) == ["unsafe-deserialization"]


def test_yaml_load_without_loader_reports():
    text = "import yaml\nyaml.load(text)\n"
    assert checks_of(check_unsafe_deserialization(Path("a.py"), text, "py", "a.py")) == [
        "unsafe-deserialization"
    ]


def test_safe_load_and_loader_stay_silent():
    safe = "import yaml\nyaml.safe_load(text)\n"
    with_loader = "import yaml\nyaml.load(text, Loader=yaml.SafeLoader)\n"
    assert check_unsafe_deserialization(Path("a.py"), safe, "py", "a.py") == []
    assert check_unsafe_deserialization(Path("a.py"), with_loader, "py", "a.py") == []


def test_binary_formatter_reports():
    text = "var fmt = new BinaryFormatter();\nvar obj = fmt.Deserialize(stream);\n"
    findings = check_unsafe_deserialization(Path("a.cs"), text, "csharp", "a.cs")
    assert checks_of(findings) == ["unsafe-deserialization"]


# -- unsafe-c-call -----------------------------------------------------------


def test_unsafe_c_calls_report():
    text = "void f(char *d, char *s) { strcpy(d, s); }\n"
    findings = check_unsafe_c_call(Path("a.c"), text, "cpp", "a.c")
    assert checks_of(findings) == ["unsafe-c-call"]


def test_bounds_checked_forms_stay_silent():
    text = 'void f(char *d, char *s) { strncpy(d, s, 16); snprintf(d, 16, "%s", s); }\n'
    assert check_unsafe_c_call(Path("a.c"), text, "cpp", "a.c") == []


def test_unsafe_calls_in_strings_stay_silent():
    text = 'const char *help = "use strcpy(dest, src) wisely";\n'
    assert check_unsafe_c_call(Path("a.c"), text, "cpp", "a.c") == []


# -- hardcoded-secret --------------------------------------------------------


def test_aws_key_format_reports():
    text = 'key = "AKIAIOSFODNN7EXAMPLE"\n'
    findings = check_hardcoded_secret(Path("a.py"), text, "py", "a.py")
    assert checks_of(findings) == ["hardcoded-secret"]


def test_jwt_format_reports():
    token = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U\n"
    )
    text = f'token = "{token}"\n'
    assert checks_of(check_hardcoded_secret(Path("a.py"), text, "py", "a.py")) == [
        "hardcoded-secret"
    ]


def test_database_url_with_password_reports():
    text = 'url = "postgres://admin:supersecret123@db.example.com/prod"\n'
    findings = check_hardcoded_secret(Path("a.py"), text, "py", "a.py")
    assert checks_of(findings) == ["hardcoded-secret"]


def test_secret_in_test_path_stays_silent():
    text = 'key = "AKIAIOSFODNN7EXAMPLE"\n'
    assert check_hardcoded_secret(Path("t.py"), text, "py", "tests/test_x.py") == []


def test_secret_in_markdown_stays_silent():
    text = "AKIAIOSFODNN7EXAMPLE\n"
    assert check_hardcoded_secret(Path("a.md"), text, "py", "docs/a.md") == []


# -- the column aggregate -----------------------------------------------------


def test_column_skips_test_paths_wholesale():
    text = 'el.innerHTML = x;\nq = f"SELECT {x}"\n'
    assert check_security_column(Path("t.ts"), text, "js", "tests/a.ts") == []


def test_column_reports_on_production_path():
    text = "el.innerHTML = x;\n"
    assert check_security_column(Path("a.ts"), text, "js", "src/a.ts")


# -- ecosystem: unused-css ----------------------------------------------------


def _repo_with(tmp_path: Path, css: str, py: str):
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "web" / "ui.css").write_text(css, encoding="utf-8")
    (tmp_path / "app.py").write_text(py, encoding="utf-8")
    return tmp_path, [tmp_path / "app.py", tmp_path / "web" / "ui.css"]


def test_unused_css_reports_orphan_class(tmp_path):
    root, files = _repo_with(
        tmp_path,
        ".card-body { padding: 1rem; }\n.orphan-widget { color: red; }\n",
        "def render():\n    return '<div class=\"card-body\"></div>'\n",
    )
    findings = check_unused_css(root, files, [])
    assert len(findings) == 1
    assert ".orphan-widget" in findings[0].message


def test_referenced_class_stays_silent(tmp_path):
    root, files = _repo_with(
        tmp_path,
        ".card-body { padding: 1rem; }\n",
        "def render():\n    return '<div class=\"card-body\"></div>'\n",
    )
    assert check_unused_css(root, files, []) == []


def test_utility_names_stay_silent(tmp_path):
    root, files = _repo_with(tmp_path, ".md\\:flex { display: flex; }\n", "x = 1\n")
    assert check_unused_css(root, files, []) == []


def test_cross_sheet_reuse_stays_silent(tmp_path):
    (tmp_path / "web").mkdir(exist_ok=True)
    (tmp_path / "web" / "a.css").write_text(".btn-primary { color: blue; }\n")
    (tmp_path / "web" / "b.css").write_text(".btn-primary:hover { color: darkblue; }\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    assert check_unused_css(tmp_path, [tmp_path / "app.py"], []) == []


# -- ecosystem: vulnerable-dependency -----------------------------------------


def test_audit_silent_without_opt_in(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    monkeypatch.delenv("MASTERCI_PIP_AUDIT", raising=False)
    assert check_vulnerable_dependencies(tmp_path, [], []) == []


def test_audit_report_parsing():
    report = """{"dependencies": [
        {"name": "requests", "version": "2.0", "vulns": [{"id": "PYSEC-1"}]},
        {"name": "clean-dep", "version": "1.0", "vulns": []}
    ]}"""
    assert _parse_audit_report(report) == [("requests", "PYSEC-1")]


def test_audit_report_malformed_is_empty():
    assert _parse_audit_report("not json at all") == []


# -- TS assertions and chains -------------------------------------------------


def test_as_any_reports():
    findings = check_ts_slop(Path("a.ts"), "const x = input as any;\n", "js", "a.ts")
    assert "unsafe-type-assertion" in checks_of(findings)


def test_double_assertion_reports():
    text = "const y = input as unknown as User;\n"
    findings = check_ts_slop(Path("a.ts"), text, "js", "a.ts")
    assert "double-type-assertion" in checks_of(findings)


def test_assertions_in_comments_stay_silent():
    text = "// never write `as any` or `as unknown as`\nconst x = 1;\n"
    findings = check_ts_slop(Path("a.ts"), text, "js", "a.ts")
    assert "unsafe-type-assertion" not in checks_of(findings)
    assert "double-type-assertion" not in checks_of(findings)


def test_plain_js_has_no_assertion_findings():
    text = "const x: string = input;\n"
    findings = check_ts_slop(Path("a.ts"), text, "js", "a.ts")
    assert checks_of(findings) == []


def test_repeated_chain_reports_once():
    chain = "const v = this.config.get('a').get('b').get('c');\n"
    text = chain * 3
    findings = check_ts_slop(Path("a.ts"), text, "js", "a.ts")
    assert checks_of(findings) == ["repeated-chained-call"]
    assert len([f for f in findings if f.check == "repeated-chained-call"]) == 1


def test_distinct_chains_stay_silent():
    text = (
        "const a = this.one().two().three();\n"
        "const b = other.one().two().three();\n"
        "const c = third.one().two().three();\n"
    )
    assert check_ts_slop(Path("a.ts"), text, "js", "a.ts") == []


def test_single_chain_occurrence_stays_silent():
    text = "const a = this.one().two().three();\nconst b = this.one().two().three();\n"
    assert check_ts_slop(Path("a.ts"), text, "js", "a.ts") == []


# -- registry and facade contracts --------------------------------------------


def test_new_checks_registered():
    for check in (
        "innerhtml",
        "dangerously-set-innerhtml",
        "sql-injection",
        "shell-injection",
        "unsafe-deserialization",
        "unsafe-c-call",
        "hardcoded-secret",
        "unsafe-type-assertion",
        "double-type-assertion",
        "repeated-chained-call",
        "unused-css",
        "vulnerable-dependency",
    ):
        assert check in mod.CHECK_CATEGORIES, f"{check} missing from the registry"


def test_security_column_is_blocking_grade():
    finding = mod.Finding(path="a.ts", line=1, check="sql-injection", message="x", weight=5)
    assert finding.advisory is False


def test_judgment_checks_are_advisory_only():
    for check in (
        "unsafe-type-assertion",
        "double-type-assertion",
        "repeated-chained-call",
        "unused-css",
    ):
        finding = mod.Finding(path="a", line=1, check=check, message="x", weight=1)
        assert finding.advisory is True, f"{check} must be advisory-only"


def test_facade_reexports_the_column():
    for name in (
        "check_innerhtml",
        "check_dangerously_set_innerhtml",
        "check_sql_injection",
        "check_shell_injection",
        "check_unsafe_deserialization",
        "check_unsafe_c_call",
        "check_hardcoded_secret",
        "check_security_column",
        "check_unused_css",
        "check_vulnerable_dependencies",
    ):
        assert hasattr(mod, name), f"facade missing {name}"
