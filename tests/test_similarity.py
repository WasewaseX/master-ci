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
    candidates, no dict-order dependence;
- the prefix-gauge fast path returns the identical best block the
    per-anchor walk returns, on seeded random diagonals of every shape
    (the equivalence is the gate: the optimization must never change
    which block wins), and a long near-copy no longer pays one walk
    per anchor line - the 4,000-line pair that took ~1.8s of
    re-walking is judged in its budget.
"""

import random
import time
from pathlib import Path

import pytest
from slop_similarity import (  # noqa: E402
    MAX_CLONES,
    _best_alignment_block,
    _best_block_by_queries,
    _grow_block,
    _qualifying_row,
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


def test_a_short_preamble_does_not_hide_a_later_clone(tmp_path):
    """Every anchor grows: the shared imports cannot suppress the block.

    Two files share two import lines, diverge for a few assignments,
    then carry a 14-line near-copy at the same offsets. Growth that
    starts only at the earliest anchor stops at the assignments -
    matched never reaches the floor - and the copy's later anchors,
    which voted for the same delta, are never tried. The block wins
    because every anchor of the alignment gets its chance.
    """
    imports = ["import json", "import os"]
    diverge_a = ["left_a = 1", "left_b = 2", "left_c = 3", "left_d = 4"]
    diverge_b = ["right_a = 11", "right_b = 12", "right_c = 13", "right_d = 14"]
    core = [
        "def shared(items, out):",
        "    total = 0",
        "    for item in items:",
        "        if not item.get('id'):",
        "            continue",
        "        total += item.get('size', 0)",
        "        out.write(str(item['id']))",
        "        out.write(', ')",
        "    if total < 0:",
        "        raise ValueError('negative total')",
        "    out.write(str(total))",
        "    return total",
        "out.flush()",
        "return len(items)",
    ]
    copied_a = list(core)
    copied_b = list(core)
    copied_b[1] = "    total = 0  # variant"
    copied_b[9] = "        raise ValueError('total is negative')"
    left = _write(
        tmp_path,
        "preamble_left.py",
        "\n".join(imports + diverge_a + copied_a) + "\n",
    )
    right = _write(
        tmp_path,
        "preamble_right.py",
        "\n".join(imports + diverge_b + copied_b) + "\n",
    )
    findings = _one(tmp_path, [left, right])
    assert len(findings) == 1, "the 14-line near-copy is a clone whatever voted first"
    message = findings[0].message
    assert "preamble_right.py:" in message
    assert findings[0].path.endswith("preamble_left.py")
    similarity = float(message.split("similarity ")[1].split(" over")[0])
    length = int(message.split("over ")[1].split(" aligned")[0])
    assert similarity >= 0.85 and length >= 14


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


# --- round-19 greptile pass: the fast path must be the walk -------------------


def _drift_periodic(right: list[str], rng: random.Random) -> list[str]:
    """One differing line every four - the every-4th-line drift shape."""
    for i in range(0, len(right), 4):
        right[i] = right[i] + "  # adjusted"
    return right


def _drift_staircase(right: list[str], rng: random.Random) -> list[str]:
    """Long exact runs separated by single-line breaks."""
    for start in range(6, len(right), 13):
        right[start] = f"value {start} = altered_{start}(data)"
    return right


def _drift_sparse(right: list[str], rng: random.Random) -> list[str]:
    """Mostly different lines, a few shared runs left behind."""
    return [
        f"other {i} = unique_{rng.randrange(10_000)}()" if rng.random() > 0.25 else line
        for i, line in enumerate(right)
    ]


def _drift_random(right: list[str], rng: random.Random) -> list[str]:
    """Scattered noise over mostly-shared lines."""
    return [
        f"other {i} = unique_{rng.randrange(10_000)}()" if rng.random() > 0.6 else line
        for i, line in enumerate(right)
    ]


_SHAPE_DRIFTS = {
    "all-match": lambda right, rng: right,
    "periodic": _drift_periodic,
    "staircase": _drift_staircase,
    "sparse": _drift_sparse,
    "random": _drift_random,
}


def _shaped_pair(rng: random.Random, n: int, shape: str) -> tuple[list[str], list[str]]:
    """One diagonal of a given shape: the same lines, some replaced."""
    left = [f"value {i} = step_{i}(data)" for i in range(n)]
    return left, _SHAPE_DRIFTS[shape](list(left), rng)


def _anchors_for(left: list[str], right: list[str], delta: int) -> list[tuple[int, int]]:
    """The real anchor pairs the shingles would vote for one alignment."""
    pairs = []
    for i in range(max(0, delta), min(len(left) - 1, len(right) - 1 + delta)):
        if left[i] == right[i - delta] and left[i + 1] == right[i + 1 - delta]:
            pairs.append((i, i - delta))
    return pairs


def _walk_best(
    left: list[str], right: list[str], delta: int, pairs: list[tuple[int, int]], same: bool
):
    """Grow every anchor directly - the semantics the fast path must keep."""
    rows = []
    file_a = 0
    file_b = 0 if same else 1
    for anchor_a, anchor_b in sorted(pairs):
        start_a, length, matched = _grow_block(left, right, anchor_a, anchor_b)
        row = _qualifying_row(file_a, file_b, delta, start_a, length, matched)
        if row is not None:
            rows.append((matched, row[0], -start_a, row))
    if not rows:
        return None
    return max(rows)[-1]


def _trial_pair(rng: random.Random, shape: str, trial: int):
    """One random diagonal: lines, delta, the anchors that vote, same-file flag."""
    n = rng.choice([2, 3, 7, 25, 60, 120])
    left, right = _shaped_pair(rng, n, shape)
    if shape == "staircase" and trial % 2 == 0:
        right = right[: max(2, n - rng.randrange(5))]  # differing lengths
    delta = rng.choice([0, 0, 0, 1, -1, 2, -2, 5, -5])
    pairs = _anchors_for(left, right, delta)
    return left, right, delta, pairs, rng.random() < 0.3


@pytest.mark.parametrize("shape", ["all-match", "periodic", "staircase", "sparse", "random"])
def test_the_query_path_returns_the_walks_best_block_on_random_diagonals(shape):
    """Every anchor's stop from the gauge equals every anchor's stop walked.

    The gate for the round's performance fix: the prefix-gauge path
    replaced one walk per anchor with two prefix lookups per anchor.
    An optimization that changed which block wins would change which
    clone reports - so seeded diagonals of every shape (exact runs,
    periodic single-line drift, sparse matches, random noise, empty
    anchors, differing file lengths, same-file overlap filters) must
    give the query path the very best row the direct walk picks.
    """
    rng = random.Random(20261121)
    for trial in range(40):
        left, right, delta, pairs, same = _trial_pair(rng, shape, trial)
        expected = _walk_best(left, right, delta, pairs, same)
        got = _best_block_by_queries(
            left, right, delta, sorted({a for a, _ in pairs}), 0, 0 if same else 1
        )
        assert got == expected, (
            f"seed trial {trial} ({shape}, n={len(left)}, delta={delta}, "
            f"{len(pairs)} anchors, same={same}): query path diverged from the walk"
        )


def test_dense_alignments_take_the_gauge_and_sparse_ones_keep_the_walk(monkeypatch):
    """The threshold routes dense alignments to the gauge, sparse to the walk."""
    lines = [f"line {i}" for i in range(40)]
    per_file = [lines, lines]
    dense = [(i, i) for i in range(30)]
    sparse = [(i, i) for i in range(8)]
    forced_walk = _best_alignment_block(per_file, 0, 1, 0, dense)
    monkeypatch.setattr("slop_similarity._DIRECT_WALK_ANCHORS", 0)
    forced_gauge = _best_alignment_block(per_file, 0, 1, 0, dense)
    assert forced_walk == forced_gauge, "routing must not change the winner"
    monkeypatch.setattr("slop_similarity._DIRECT_WALK_ANCHORS", 10_000)
    assert _best_alignment_block(per_file, 0, 1, 0, sparse) is not None


def test_a_long_near_copy_is_one_gauge_pass_not_one_walk_per_anchor(tmp_path):
    """The 4,000-line near-copy pair that took ~1.8s of re-walking.

    A long near-copy votes roughly one anchor per copied line, so
    growing every anchor re-walked the whole block once per anchor -
    quadratic in the copy's length, ~1.8s for a 4,000-line pair. The
    gauge answers every anchor's stop from one prefixed pass; the pair
    still reports (the same block, most matched evidence first) inside
    a budget the re-walking path cannot meet.
    """
    n = 4000
    lines = [f"row = transform_{i}(payload, {i})" for i in range(n)]
    left = tmp_path / "big_left.py"
    right = tmp_path / "big_right.py"
    left.write_text("\n".join(lines) + "\n", encoding="utf-8")
    drifted = list(lines)
    for i in range(0, n, 4):
        drifted[i] = drifted[i] + "  # adjusted"
    right.write_text("\n".join(drifted) + "\n", encoding="utf-8")
    started = time.perf_counter()
    findings = check_near_duplicates(None, [left, right])
    elapsed = time.perf_counter() - started
    assert len(findings) == 1, "the long near-copy is still one clone"
    message = findings[0].message
    score = float(message.split("similarity ")[1].split(" over")[0])
    length = int(message.split("over ")[1].split(" aligned")[0])
    assert score >= 0.7 and length >= 3000, f"the block survived: {score} over {length}"
    assert elapsed < 1.0, f"the long pair took {elapsed:.2f}s - the re-walk is back"
