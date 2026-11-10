"""Contract tests for the new-code gates (roadmap item 7).

Covers the documented contract: blocking findings only count when they
land on lines the change actually adds (legacy debt never blocks), the
cognitive/cyclomatic ceilings apply to functions the diff touches, an
added file is new code in full, and the fail-closed semantics (missing
diff base or git failure is exit 2, never a silent pass).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import new_code_gate as ng  # noqa: E402
from slop_cognitive import _TS_OK  # noqa: E402
from slop_lizard import _LIZARD_OK  # noqa: E402
from slop_model import Finding  # noqa: E402

needs_lizard = pytest.mark.skipif(
    not _LIZARD_OK, reason="lizard not installed: parse path degrades to the line scanner"
)

needs_treesitter = pytest.mark.skipif(
    not _TS_OK,
    reason="tree-sitter not installed: cognitive facts report nothing rather than guessing",
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "mod.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _commit(root: Path, message: str) -> None:
    _git(root, "add", ".")
    _git(root, "commit", "-qm", message)


def _base(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD~1").strip()


def test_slop_finding_on_new_line_breaches(repo: Path) -> None:
    (repo / "bad.py").write_text(
        "def handle(req):\n    try:\n        return req\n    except Exception:\n        pass\n",
        encoding="utf-8",
    )
    _commit(repo, "add a swallowed exception")
    exit_code, _, findings = ng.run_gate(
        repo, diff_base=_base(repo), max_slop=0, max_cognitive=0, max_cyclomatic=0
    )
    assert exit_code == 1
    assert any(item.check == "swallowed-exception" for item in findings)


def test_slop_finding_on_untouched_line_holds(repo: Path) -> None:
    # The blocking defect exists in the base commit; the change only adds
    # a clean function elsewhere. Legacy debt never blocks the gate.
    (repo / "bad.py").write_text(
        "def handle(req):\n    try:\n        return req\n    except Exception:\n        pass\n",
        encoding="utf-8",
    )
    _commit(repo, "legacy debt lands in base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "mod.py").write_text(
        "def a():\n    return 1\n\n\ndef b():\n    return 2\n", encoding="utf-8"
    )
    _commit(repo, "clean change on top")
    exit_code, _, findings = ng.run_gate(
        repo, diff_base=base, max_slop=0, max_cognitive=0, max_cyclomatic=0
    )
    assert exit_code == 0
    # advisory findings may land on the new lines; nothing blocking may
    assert not any(not item.advisory for item in findings)


def test_added_file_is_new_code_in_full(repo: Path) -> None:
    (repo / "new.py").write_text(
        "def handle(req):\n    try:\n        return req\n    except Exception:\n        pass\n",
        encoding="utf-8",
    )
    _commit(repo, "whole file is added")
    exit_code, _, _ = ng.run_gate(
        repo, diff_base=_base(repo), max_slop=0, max_cognitive=0, max_cyclomatic=0
    )
    assert exit_code == 1


@needs_lizard
@needs_treesitter
def test_cognitive_ceiling_breaches_on_new_function(repo: Path) -> None:
    (repo / "twisted.py").write_text(
        "def twisted(data):\n    for a in data:\n        if a:\n            for b in a:\n"
        "                if b:\n                    for c in b:\n                        if c:\n"
        "                            return c\n    return None\n",
        encoding="utf-8",
    )
    _commit(repo, "add a reader-effort monster")
    exit_code, sections, _ = ng.run_gate(
        repo, diff_base=_base(repo), max_slop=100, max_cognitive=15, max_cyclomatic=0
    )
    assert exit_code == 1
    assert "complexity of touched functions" in sections[0]


@needs_lizard
@needs_treesitter
def test_legacy_fat_function_untouched_holds(repo: Path) -> None:
    (repo / "twisted.py").write_text(
        "def twisted(data):\n    for a in data:\n        if a:\n            for b in a:\n"
        "                if b:\n                    for c in b:\n                        if c:\n"
        "                            return c\n    return None\n",
        encoding="utf-8",
    )
    _commit(repo, "legacy monster lands in base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "mod.py").write_text(
        "def a():\n    return 1\n\n\ndef b():\n    return 2\n", encoding="utf-8"
    )
    _commit(repo, "clean change, monster untouched")
    exit_code, _, _ = ng.run_gate(
        repo, diff_base=base, max_slop=100, max_cognitive=15, max_cyclomatic=0
    )
    assert exit_code == 0


@needs_lizard
def test_cyclomatic_ceiling_breaches(repo: Path) -> None:
    (repo / "twisted.py").write_text(
        "def twisted(data):\n    for a in data:\n        if a:\n            for b in a:\n"
        "                if b:\n                    for c in b:\n                        if c:\n"
        "                            return c\n    return None\n",
        encoding="utf-8",
    )
    _commit(repo, "ccn 7 against a ceiling of 5")
    exit_code, _, _ = ng.run_gate(
        repo, diff_base=_base(repo), max_slop=100, max_cognitive=0, max_cyclomatic=5
    )
    assert exit_code == 1


def test_missing_diff_base_is_exit_2(repo: Path) -> None:
    with pytest.raises(ng.NewCodeGateError, match="diff-base is empty"):
        ng.run_gate(repo, diff_base="", max_slop=0, max_cognitive=0, max_cyclomatic=0)


def test_unknown_diff_base_is_exit_2(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ng.main([str(repo), "--diff-base", "no-such-ref", "--max-slop-score", "0"]) == 2
    assert "new-code gate rejected" in capsys.readouterr().err


def test_no_ceiling_requested_short_circuits(repo: Path) -> None:
    assert ng.run_gate(repo, diff_base="", max_slop=100, max_cognitive=0, max_cyclomatic=0) == (
        0,
        [],
        [],
    )


def test_findings_on_new_lines_matches_relative_and_absolute(tmp_path: Path) -> None:
    target = tmp_path / "x.py"
    changed = {"x.py": {4}}
    absolute = Finding(path=str(target), line=4, check="c", message="m", weight=1)
    relative = Finding(path="x.py", line=4, check="c", message="m", weight=1)
    outside = Finding(path="x.py", line=5, check="c", message="m", weight=1)
    kept = ng.findings_on_new_lines(tmp_path, [absolute, relative, outside], changed)
    assert {id(item) for item in kept} == {id(absolute), id(relative)}


@needs_lizard
def test_touched_functions_span_logic(tmp_path: Path) -> None:
    source = "def one():\n    x = 1\n    return x\n\n\ndef two():\n    y = 2\n    return y\n"
    (tmp_path / "m.py").write_text(source, encoding="utf-8")
    touched = {p.name for p in ng.touched_functions(tmp_path, {"m.py": {2}})}
    assert touched == {"one"}
    touched = {p.name for p in ng.touched_functions(tmp_path, {"m.py": {6}})}
    assert touched == {"two"}


# The v2.5 parser guarantee: a ceiling without its parser is exit 2, never
# a silent "0 functions touched" pass.


def test_cyclomatic_ceiling_without_lizard_is_exit_2(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ng, "lizard_available", lambda: False)
    with pytest.raises(ng.NewCodeGateError, match="lizard is not importable"):
        ng.run_gate(repo, diff_base="HEAD", max_slop=0, max_cognitive=0, max_cyclomatic=15)


def test_cognitive_ceiling_without_tree_sitter_is_exit_2(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ng, "tree_sitter_available", lambda: False)
    with pytest.raises(ng.NewCodeGateError, match="tree-sitter"):
        ng.run_gate(repo, diff_base="HEAD", max_slop=0, max_cognitive=15, max_cyclomatic=0)


def test_no_ceiling_requested_needs_no_parser(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ng, "lizard_available", lambda: False)
    monkeypatch.setattr(ng, "tree_sitter_available", lambda: False)
    code, sections, findings = ng.run_gate(
        repo, diff_base="HEAD", max_slop=100, max_cognitive=0, max_cyclomatic=0
    )
    assert code == 0
    assert sections == []
    assert findings == []


# The detector side: --require-parsers fails closed (exit 2) when an
# analyzer is missing - the CI jobs install the pins and demand them.


def test_require_parsers_fails_closed_without_lizard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import slop_cli

    monkeypatch.setattr(slop_cli, "lizard_available", lambda: False)
    assert slop_cli.main([".", "--require-parsers"]) == 2
    assert "lizard" in capsys.readouterr().err


def test_require_parsers_lists_every_missing_analyzer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import slop_cli

    monkeypatch.setattr(slop_cli, "lizard_available", lambda: False)
    monkeypatch.setattr(slop_cli, "tree_sitter_available", lambda: False)
    assert slop_cli.main([".", "--require-parsers"]) == 2
    err = capsys.readouterr().err
    assert "lizard" in err
    assert "tree-sitter" in err


def test_require_parsers_passes_when_analyzers_are_importable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import slop_cli

    monkeypatch.setattr(slop_cli, "lizard_available", lambda: True)
    monkeypatch.setattr(slop_cli, "tree_sitter_available", lambda: True)
    assert slop_cli.main([str(tmp_path), "--require-parsers"]) != 2
