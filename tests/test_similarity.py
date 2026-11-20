"""Contract tests for the near-duplicate similarity oracle (A9, round-19).

The documented contracts, test-pinned:

- cross-file near-clones are reported with the score as evidence - the
    message names both locations and the similarity, the finding points
    at the first block;
- the clean end stays silent: distinct files, files below the match
    floor, and boilerplate whose shingles exceed the owner cap (the
    exact-duplication checks own that case) produce nothing;
- same-file clones are found when the regions do not overlap, and a
    file never accuses itself through stride-one self-overlap;
- one clone reports once no matter how many anchors voted for it, and
    the findings cap holds at MAX_CLONES best-similarity rows;
- identical inputs give identical findings - stable hashes, sorted
    candidates, no dict-order dependence.
"""

from pathlib import Path

from slop_similarity import (  # noqa: E402
    MAX_CLONES,
    check_near_duplicates,
)

_CORE = [
    "fields = ['id', 'name', 'total']",
    "for row in rows:",
    "    cells = [str(row['id']), str(row['name']), str(row['total'])]",
    "    if not cells[1]:",
    "        cells[1] = 'unknown'",
    "    if not cells[2]:",
    "        cells[2] = '0'",
    "    if len(cells) != len(fields):",
    "        raise ValueError('row shape drifted')",
    "    out.write(sep.join(cells))",
    "    out.write(term)",
    "out.flush()",
    "return len(rows)",
]


def _module(header: str, core: list[str], variation: tuple[int, str]) -> str:
    lines = [header]
    for index, line in enumerate(core):
        lines.append(variation[1] if index == variation[0] else line)
    return "\n".join(lines) + "\n"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _one(tmp_path: Path, files: list[Path]):
    return check_near_duplicates(None, files)


def test_cross_file_near_clone_carries_the_score_and_both_locations(tmp_path):
    left = _write(
        tmp_path,
        "render_csv.py",
        _module(
            "SEP = ','\nTERMINATOR = '\\n'\n\ndef render_csv(rows, out):\n",
            _CORE,
            (4, "        cells[1] = 'unknown'"),
        ),
    )
    right = _write(
        tmp_path,
        "render_tsv.py",
        _module(
            "SEP = '\\t'\nTERMINATOR = '\\r\\n'\n\ndef render_tsv(rows, out):\n",
            _CORE,
            (4, "        cells[1] = 'anonymous'"),
        ),
    )
    findings = _one(tmp_path, [left, right])
    assert len(findings) == 1
    message = findings[0].message
    assert "render_tsv.py:" in message
    assert "similarity 0.9" in message
    assert findings[0].check == "near-duplicate-clone"
    assert findings[0].path.endswith("render_csv.py")
    assert findings[0].line >= 1


def test_distinct_files_stay_silent(tmp_path):
    left = _write(
        tmp_path,
        "export_json.py",
        "def export_json(rows):\n    payload = []\n    for row in rows:\n"
        "        if not row.get('active'):\n            continue\n"
        "        payload.append({'id': row['id'], 'sum': row['total']})\n"
        "    return {'items': payload, 'count': len(payload)}\n",
    )
    right = _write(
        tmp_path,
        "export_xml.py",
        "def export_xml(rows, out):\n    out.write('<items>')\n    total = 0\n"
        "    for row in rows:\n        total += row['total']\n"
        "        out.write(f\"<item id='{row['id']}'/>\")\n"
        "    out.write('</items>')\n    return total\n",
    )
    assert _one(tmp_path, [left, right]) == []


def test_below_the_match_floor_stays_silent(tmp_path):
    left = _write(tmp_path, "left.py", "\n".join(_CORE[:6]) + "\n    return 1\n")
    right = _write(
        tmp_path,
        "right.py",
        "\n".join(_CORE[:6]).replace("cells[1]", "cells[9]") + "\n    return 2\n",
    )
    assert _one(tmp_path, [left, right]) == []


def test_boilerplate_beyond_the_owner_cap_loses_cross_file_candidacy(tmp_path):
    """A shingle with more owners than the cap keeps only its first 24.

    Two files built from the same 30x repeated guard pattern share
    hundreds of positions per shingle; the cap keeps the earliest ones
    (both same-file), so the cross-file alignment never reaches the
    vote floor and guard_b never accuses guard_a - the repeated lines
    stay the exact-duplication oracle's case.
    """
    noisy = ("    if not row:\n        continue\n" * 30)[:-1]
    left = _write(tmp_path, "guard_a.py", "def guard_a(rows):\n" + noisy + "\n    return 1\n")
    right = _write(tmp_path, "guard_b.py", "def guard_b(rows):\n" + noisy + "\n    return 2\n")
    findings = _one(tmp_path, [left, right])
    assert all("guard_b" not in finding.message for finding in findings)


def test_overlapping_self_regions_never_report(tmp_path):
    """Same-file rows are always disjoint, whatever the pattern's period."""
    noisy = ("    if not row:\n        continue\n" * 30)[:-1]
    path = _write(tmp_path, "guard.py", "def guard(rows):\n" + noisy + "\n    return 1\n")
    findings = _one(tmp_path, [path])
    assert findings, "the disjoint self-copies of the pattern are real clones"
    for finding in findings:
        start_a = finding.line
        start_b = int(finding.message.split("guard.py:")[1].split(" ")[0])
        length = int(finding.message.split("over ")[1].split(" aligned")[0])
        assert start_b >= start_a + length or start_a >= start_b + length


def test_same_file_disjoint_copies_are_found(tmp_path):
    body = "\n".join(_CORE)
    path = _write(
        tmp_path,
        "twins.py",
        "def first(rows, out):\n" + body + "\n\ndef second(rows, out):\n" + body + "\n",
    )
    findings = _one(tmp_path, [path])
    assert len(findings) == 1
    assert "twins.py:" in findings[0].message


def test_a_file_never_accuses_itself_through_self_overlap(tmp_path):
    body = "\n".join(_CORE)
    path = _write(tmp_path, "solo.py", "def one(rows, out):\n" + body + "\n")
    assert _one(tmp_path, [path]) == []


def test_one_clone_reports_once_and_the_cap_holds(tmp_path):
    base = _write(
        tmp_path,
        "base.py",
        _module("def base(rows, out):\n", _CORE, (4, "        cells[1] = 'unknown'")),
    )
    copies = []
    for index in range(5):
        copies.append(
            _write(
                tmp_path,
                f"copy{index}.py",
                _module(
                    f"def copy{index}(rows, out):\n", _CORE, (4, f"        cells[1] = 'v{index}'")
                ),
            )
        )
    findings = _one(tmp_path, [base] + copies)
    assert len(findings) == MAX_CLONES
    scores = [float(f.message.split("similarity ")[1].split(" over")[0]) for f in findings]
    assert scores == sorted(scores, reverse=True)
    keys = {(f.path, f.line, f.message) for f in findings}
    assert len(keys) == MAX_CLONES


def test_deterministic_across_runs(tmp_path):
    files = [
        _write(
            tmp_path, "a.py", _module("def a(rows, out):\n", _CORE, (4, "        cells[1] = 'x'"))
        ),
        _write(
            tmp_path, "b.py", _module("def b(rows, out):\n", _CORE, (4, "        cells[1] = 'y'"))
        ),
        _write(
            tmp_path, "c.py", _module("def c(rows, out):\n", _CORE, (4, "        cells[2] = 'z'"))
        ),
    ]
    first = [dict(path=f.path, line=f.line, message=f.message) for f in _one(tmp_path, files)]
    second = [dict(path=f.path, line=f.line, message=f.message) for f in _one(tmp_path, files)]
    assert first == second


def test_comment_and_blank_noise_does_not_break_the_alignment(tmp_path):
    """Normalization drops blanks and comments, so noise-free comparison."""
    noisy = "\n".join(
        [
            _CORE[0],
            "# a comment the oracle must ignore",
            "",
            *_CORE[1:6],
            "// another comment",
            *_CORE[6:],
        ]
    )
    left = _write(tmp_path, "noisy.py", "def noisy(rows, out):\n" + noisy + "\n")
    right = _write(tmp_path, "plain.py", "def plain(rows, out):\n" + "\n".join(_CORE) + "\n")
    findings = _one(tmp_path, [left, right])
    assert len(findings) == 1
    assert "similarity 1.00" in findings[0].message


def test_fixture_trees_judge_through_the_probe(tmp_path):
    root = Path(__file__).resolve().parents[1] / "fixtures" / "bench"
    detected = check_near_duplicates(
        root / "near-duplicate-clone", sorted((root / "near-duplicate-clone").rglob("*.py")), []
    )
    clean = check_near_duplicates(
        root / "near-duplicate-clone-clean",
        sorted((root / "near-duplicate-clone-clean").rglob("*.py")),
        [],
    )
    assert len(detected) == 1 and detected[0].check == "near-duplicate-clone"
    assert clean == []
