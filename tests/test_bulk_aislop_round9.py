"""Round-9 bulk adoption: the last two Python columns of the aislop catalog.

The scanaislop 58-rule matrix is closed with the Python broad-catch and
the Python print() debug oracle - the only two rules the earlier rounds
had not adopted under another id. Contract tests pin the positive, the
nearest negative and the advisory flag for every new rule, the same law
test_bulk_aislop establishes: a future edit that flips advisory-ness
fails here.

Module loads per the conftest convention: scripts/ is already on
sys.path, so the engines import directly and no preamble repeats.
"""

from detector_module import hits as _hits, scan

BROAD = "def run_it():\n    try:\n        work()\n    except {clause}:\n        {body}\n"


# ---------------------------------------------------------------------------
# broad-except (reliability, advisory - the C# sibling's grade)
# ---------------------------------------------------------------------------


def test_broad_except_flagged_with_handling_body(tmp_path):
    code = BROAD.format(clause="Exception", body='log.warning("failed")')
    findings = scan(tmp_path, {"svc.py": code})
    hits = _hits(findings, "broad-except")
    assert len(hits) == 1
    assert hits[0].advisory
    assert "Exception" in hits[0].message


def test_broad_except_base_exception_flagged(tmp_path):
    code = BROAD.format(clause="BaseException", body='log.warning("failed")')
    assert _hits(scan(tmp_path, {"svc.py": code}, sub="base"), "broad-except")


def test_broad_except_tuple_member_flagged(tmp_path):
    code = BROAD.format(clause="(ValueError, Exception)", body='log.warning("x")')
    assert _hits(scan(tmp_path, {"svc.py": code}, sub="tup"), "broad-except")


def test_broad_except_specific_type_not_flagged(tmp_path):
    code = BROAD.format(clause="ValueError", body='log.warning("x")')
    findings = scan(tmp_path, {"svc.py": code}, sub="narrow")
    assert not _hits(findings, "broad-except")
    assert not _hits(findings, "bare-except")


def test_broad_except_lone_reraise_not_flagged(tmp_path):
    """A translation point re-raises; SonarQube's rule exempts it too."""
    code = BROAD.format(clause="Exception", body="raise")
    findings = scan(tmp_path, {"svc.py": code}, sub="reraise")
    assert not _hits(findings, "broad-except")


def test_broad_except_declared_noqa_not_flagged(tmp_path):
    """``# noqa`` on the except line is declared intent, like any directive."""
    code = BROAD.format(clause="Exception:  # noqa: BLE001 - top boundary", body="alert()")
    findings = scan(tmp_path, {"svc.py": code}, sub="noqa")
    assert not _hits(findings, "broad-except")


def test_broad_except_pass_only_owned_by_swallowed_exception(tmp_path):
    """One finding per handler: swallowed-exception, not a double flag."""
    code = BROAD.format(clause="Exception", body="pass")
    findings = scan(tmp_path, {"svc.py": code}, sub="pass")
    assert _hits(findings, "swallowed-exception")
    assert not _hits(findings, "broad-except")


def test_bare_except_still_blocking_grade(tmp_path):
    code = BROAD.format(clause="", body='log.warning("x")')
    findings = scan(tmp_path, {"svc.py": code}, sub="bare")
    hits = _hits(findings, "bare-except")
    assert len(hits) == 1 and not hits[0].advisory


def test_broad_except_fires_in_test_paths_like_its_sibling(tmp_path):
    """The Python AST family never test-gates: bare-except and
    swallowed-exception grade test code the same as production - so does
    broad-except. Pinned here so a future edit cannot gate silently."""
    code = BROAD.format(clause="Exception", body='log.warning("x")')
    findings = scan(tmp_path, {"tests/test_svc.py": "import pytest\n" + code}, sub="t")
    assert _hits(findings, "broad-except")
    assert not _hits(findings, "bare-except")


# ---------------------------------------------------------------------------
# Python debug-print (code-health, advisory - the other families' grade)
# ---------------------------------------------------------------------------


PROD_PRINT = 'def run_it(x):\n    print(x)\n    print("dbg", x)\n    return x\n'


def test_py_print_debug_flagged_in_production(tmp_path):
    findings = scan(tmp_path, {"svc.py": PROD_PRINT})
    hits = _hits(findings, "debug-print")
    assert len(hits) == 1
    assert "2x" in hits[0].message
    assert hits[0].advisory
    assert hits[0].line == 2


def test_py_print_in_string_or_comment_not_flagged(tmp_path):
    """AST oracle: text inside strings and comments is not a call."""
    code = 'def run_it(x):\n    # print(x)\n    return "print(x)"\n'
    findings = scan(tmp_path, {"svc.py": code}, sub="quiet")
    assert not _hits(findings, "debug-print")


def test_py_print_in_main_guard_file_not_flagged(tmp_path):
    """A file that runs as a tool is its own consumer."""
    code = PROD_PRINT + '\n\nif __name__ == "__main__":\n    run_it(1)\n'
    findings = scan(tmp_path, {"tool.py": code}, sub="guard")
    assert not _hits(findings, "debug-print")


def test_py_print_in_script_dir_not_flagged(tmp_path):
    findings = scan(tmp_path, {"scripts/tool.py": PROD_PRINT}, sub="scriptdir")
    assert not _hits(findings, "debug-print")


def test_py_print_in_entrypoint_basename_not_flagged(tmp_path):
    for name in ("cli.py", "main.py", "__main__.py", "conftest.py"):
        findings = scan(tmp_path, {name: PROD_PRINT}, sub=f"entry-{name}")
        assert not _hits(findings, "debug-print"), name


def test_py_print_in_test_path_not_flagged(tmp_path):
    findings = scan(tmp_path, {"tests/test_svc.py": "import pytest\n\n" + PROD_PRINT}, sub="tst")
    assert not _hits(findings, "debug-print")


def test_other_families_debug_print_unchanged(tmp_path):
    """The cross-family grade stays: console.log still files debug-print."""
    findings = scan(tmp_path, {"app.js": "function f(x) { console.log(x); }\n"})
    assert _hits(findings, "debug-print")
