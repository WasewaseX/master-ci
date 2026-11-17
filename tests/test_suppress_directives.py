"""Round-11 suppression directives: the scoped inline opt-out.

The old doctrine (every inline ``master-ci-ignore`` is an invented
suppression) is replaced by scanaislop's design: canonical scopes
suppress, unknown scopes are reported with a did-you-mean hint. Every
law here is pinned on the positive, the nearest negative and the
visibility counter - a future edit that flips any of them fails this
module.
"""

import json
from pathlib import Path

from detector_module import hits as _hits, scan
from slop_suppress import closest_scope, is_functional, parse_file_suppression

# The one-line try/except fixture the scope matrix pins. The blocking
# check the detector emits for it is swallowed-exception (line of the
# except clause); every scope test suppresses or preserves exactly it.
CATCH = (
    "def run():\n"
    "    try:\n"
    "        work()\n"
    "    except Exception:\n"
    "        pass\n"
)


def run_svc(tmp_path, code, name="svc.py"):
    """One fixture file, one scan - keeps the shared scan idiom out of
    the cross-file line-duplication ledger this repo enforces."""
    return scan(tmp_path, {name: code})



def test_control_without_directive_fires(tmp_path):
    findings = scan(tmp_path, {"svc.py": CATCH})
    assert _hits(findings, "swallowed-exception")


def test_same_line_directive_suppresses_blocking(tmp_path):
    code = CATCH.replace(
        "    except Exception:\n", "    except Exception:  # master-ci-ignore-line\n"
    )
    findings = run_svc(tmp_path, code)
    assert not _hits(findings, "swallowed-exception")


def test_bare_directive_equals_same_line(tmp_path):
    code = CATCH.replace(
        "    except Exception:\n", "    except Exception:  # master-ci-ignore\n"
    )
    findings = run_svc(tmp_path, code)
    assert not _hits(findings, "swallowed-exception")


def test_next_line_directive_suppresses_the_following_line(tmp_path):
    # Trailing on the last try-body line: the next line is the except.
    code = CATCH.replace(
        "        work()\n", "        work()  # master-ci-ignore-next-line\n"
    )
    findings = run_svc(tmp_path, code)
    assert not _hits(findings, "swallowed-exception")


def test_next_line_scope_does_not_reach_two_lines_down(tmp_path):
    # Trailing on the def line: the next line opens the try, the catch
    # is two lines below - out of scope, so the finding survives.
    code = CATCH.replace("def run():\n", "def run():  # master-ci-ignore-next-line\n")
    findings = run_svc(tmp_path, code)
    assert _hits(findings, "swallowed-exception")


def test_file_scope_suppresses_advisory_but_not_blocking(tmp_path):
    code = "# master-ci-ignore-file\n" + CATCH
    findings = run_svc(tmp_path, code)
    assert _hits(findings, "swallowed-exception")


def test_file_scope_suppresses_advisory_finding(tmp_path):
    code = "# master-ci-ignore-file\ndef orphan_helper():\n    return 42\n"
    findings = scan(tmp_path, {"app.py": code})
    assert not _hits(findings, "unused-declaration")


def test_unknown_scope_suppresses_nothing_and_is_reported(tmp_path):
    code = "# master-ci-ignore-nextline\n" + CATCH
    findings = run_svc(tmp_path, code)
    unknown = _hits(findings, "unknown-directive")
    assert len(unknown) == 1
    assert "next-line" in unknown[0].message
    assert _hits(findings, "swallowed-exception"), "the typo suppressed nothing"


def test_unknown_scope_without_a_close_match_lists_the_scopes(tmp_path):
    code = "# master-ci-ignore-whatever\nvalue = compute()\n"
    findings = scan(tmp_path, {"app.py": code})
    unknown = _hits(findings, "unknown-directive")
    assert unknown and "master-ci-ignore-file" in unknown[0].message


def test_directive_inside_string_literal_is_neither(tmp_path):
    code = 'TABLE = {"hint": "master-ci-ignore-next-line"}\nvalue = compute()\n'
    findings = scan(tmp_path, {"app.py": code})
    assert not _hits(findings, "unknown-directive")
    table = tmp_path / "tbl.py"
    positions, unknown = parse_file_suppression(table)
    assert not unknown
    assert not positions.same and not positions.following and not positions.whole_file


def test_directive_inside_docstring_is_neither(tmp_path):
    code = (
        'def run():\n'
        '    """Use master-ci-ignore-next-line to silence a line."""\n'
        + CATCH
    )
    findings = scan(tmp_path, {"app.py": code})
    assert _hits(findings, "swallowed-exception")
    assert not _hits(findings, "unknown-directive")


def test_js_trailing_directive(tmp_path):
    code = "work(); // master-ci-ignore-line\n"
    findings = scan(tmp_path, {"svc.js": code})
    assert not _hits(findings, "unknown-directive")


def test_closest_scope_suggestion_map():
    assert closest_scope("nextline") == "next-line"
    assert closest_scope("NEXT-LINE") == "next-line"
    assert closest_scope("whatever") is None


def test_is_functional_matches_canonical_only():
    assert is_functional("# master-ci-ignore-line")
    assert is_functional("# master-ci-ignore-next-line")
    assert is_functional("# master-ci-ignore")
    assert not is_functional("# master-ci-ignore-nextline")


def test_suppressed_count_is_visible(tmp_path):
    """scan() suppresses inline itself; the count rides the metrics bundle."""
    (tmp_path / "svc.py").write_text(
        "def run():\n"
        "    try:\n"
        "        work()\n"
        "    except Exception:  # master-ci-ignore-line\n"
        "        pass\n"
    )
    from slop_files import load_phrases
    from slop_scan import scan as raw_scan

    bundle: dict = {}
    findings = raw_scan(tmp_path, load_phrases(), metrics_out=bundle)
    # The suppressed finding is gone and the bookkeeping says how many.
    assert not _hits(findings, "swallowed-exception")
    assert bundle.get("inline_suppressed", 0) >= 1


def test_cli_json_reports_the_inline_counter(tmp_path):
    """--json carries the suppression bookkeeping (visibility contract)."""
    (tmp_path / "svc.py").write_text(
        "def run():\n"
        "    try:\n"
        "        work()\n"
        "    except Exception:  # master-ci-ignore-line\n"
        "        pass\n"
    )
    import subprocess
    import sys as _sys

    from slop_files import load_phrases
    from slop_scan import scan as raw_scan

    bundle: dict = {}
    raw_scan(tmp_path, load_phrases(), metrics_out=bundle)
    out = tmp_path / "out.json"
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            _sys.executable,
            str(repo_root / "scripts" / "slop_cli.py"),
            str(tmp_path),
            "--json",
            str(out),
            "--max-score",
            "99",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode in (0, 1), proc.stderr[-500:]
    data = json.loads(out.read_text())
    assert data["suppressed"]["inline"] == bundle.get("inline_suppressed", 0)
    assert data["suppressed"]["inline"] >= 1
