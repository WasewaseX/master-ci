"""Near-duplicate clone detection with similarity scores (roadmap A9).

Exact duplication is already measured: ``slop_duplicates.py`` counts
repeated lines and ``dup-block-density`` measures contiguous identical
runs. What neither answers is the near-duplicate - two blocks that
differ in a handful of lines but carry the same logic, the clone class
SonarQube and jscpd price by similarity rather than by identity. This
module closes that gap:

- code files are normalized (blank and comment-only lines dropped, the
    comparison reads code, not formatting) and indexed by
    pair-shingles: every two consecutive normalized lines hash to one
    key. A key two positions share is an anchor.
- anchors vote on alignment: every anchor pair votes for its
    (file pair, position delta), and only alignments with at least
    ``VOTE_FLOOR`` votes grow a block. A real clone votes many times
    for one delta; scattered repeated lines never reach the floor, so
    the cost grows with actual similarity, not with the square of the
    file count, and the index keeps only the first ``MAX_OWNERS``
    positions per shingle - the boilerplate ``slop_duplicates``
    already judges, and it cannot flood the vote.
- a passing alignment grows outward from its anchor while the running
    mismatch ratio stays inside ``MISMATCH_RATIO``; the block reports
    when ``MIN_MATCHED`` matched lines survive and the similarity -
    matched over aligned length - clears ``SIM_THRESHOLD``. The score
    is the evidence: the message names both locations and the
    similarity, so a review starts from a number, not a vibe.

Findings are capped at ``MAX_CLONES``, best similarity first, the same
cap every repo-level check carries. A file's own overlapping regions
never accuse each other, one aligned pair reports once no matter how
many anchors voted for it, and identical-input runs are deterministic:
sorted candidates, stable hashes, no dict-order dependence. Advisory
by registry - a measurement, not a defect verdict. The exact end of
the spectrum stays with the existing duplication checks; this oracle
only speaks where blocks are similar, not identical.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from itertools import islice
from pathlib import Path

from slop_files import is_ignored, read_text_lax
from slop_model import Finding
from slop_patterns import CODE_EXTS

SIM_THRESHOLD = 0.7
MIN_MATCHED = 8
MISMATCH_RATIO = 0.25
VOTE_FLOOR = 5
MAX_OWNERS = 24
MAX_CLONES = 3

_COMMENT_PREFIXES = ("#", "//", "*", "<!--")


def _normalized_lines(text: str) -> tuple[list[str], list[int]]:
    """(stripped code lines, original linenos), blanks and comments out."""
    lines: list[str] = []
    linenos: list[int] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(_COMMENT_PREFIXES):
            continue
        lines.append(line)
        linenos.append(lineno)
    return lines, linenos


def _shingle(left: str, right: str) -> str:
    """A stable digest of two consecutive lines."""
    return hashlib.sha256(f"{left}\x00{right}".encode("utf-8")).hexdigest()[:16]


def _ignored(path: Path, root: Path, globs: list[str]) -> bool:
    """The same relative-path rule the per-file scan applies."""
    try:
        rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
    except ValueError:
        rel = path.as_posix()
    return is_ignored(rel, globs)


def _index_anchors(per_file: list[list[str]]) -> dict[str, list[tuple[int, int]]]:
    """pair-shingle -> up to MAX_OWNERS (file index, line index) positions."""
    index: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for file_idx, lines in enumerate(per_file):
        for pos in range(len(lines) - 1):
            owners = index[_shingle(lines[pos], lines[pos + 1])]
            if len(owners) < MAX_OWNERS:
                owners.append((file_idx, pos))
    return index


def _ordered_pair(
    left: tuple[int, int], right: tuple[int, int]
) -> tuple[tuple[int, int], tuple[int, int]]:
    """The pair sorted so the smaller (file, position) side leads."""
    return (left, right) if left <= right else (right, left)


def _vote_pair(
    votes: dict[tuple[int, int, int], list[tuple[int, int]]],
    left: tuple[int, int],
    right: tuple[int, int],
) -> None:
    """One anchor pair's vote, normalized to one direction per alignment."""
    (file_a, pos_a), (file_b, pos_b) = _ordered_pair(left, right)
    if file_a == file_b and pos_a == pos_b:
        return
    votes[(file_a, file_b, pos_a - pos_b)].append((pos_a, pos_b))


def _votes(
    index: dict[str, list[tuple[int, int]]],
) -> dict[tuple[int, int, int], list[tuple[int, int]]]:
    """(file_a, file_b, delta) -> the anchors that voted for the alignment.

    Every unordered position pair votes once; the vote is normalized so
    the smaller (file, position) side leads, which keeps the delta
    deterministic and stops the same alignment from counting twice.
    """
    votes: dict[tuple[int, int, int], list[tuple[int, int]]] = defaultdict(list)
    for owners in index.values():
        if len(owners) < 2:
            continue
        for position, left in enumerate(owners):
            for right in islice(owners, position + 1, None):
                _vote_pair(votes, left, right)
    return votes


def _grow_block(
    lines_a: list[str], lines_b: list[str], anchor_a: int, anchor_b: int
) -> tuple[int, int, int]:
    """Extend one anchor pair outward; (start_a, length, matched).

    The anchor shingle - two matched lines - extends both ways while
    the mismatch ratio stays inside ``MISMATCH_RATIO``; a line that
    would breach the ratio ends the walk and stays out of the block.
    """
    matched = 2
    length = 2
    start_a = anchor_a
    left, right = anchor_a - 1, anchor_b - 1
    while left >= 0 and right >= 0:
        next_matched = matched + (1 if lines_a[left] == lines_b[right] else 0)
        next_length = length + 1
        if next_length - next_matched > MISMATCH_RATIO * next_length:
            break
        matched, length = next_matched, next_length
        start_a = left
        left -= 1
        right -= 1
    left, right = anchor_a + 2, anchor_b + 2
    while left < len(lines_a) and right < len(lines_b):
        next_matched = matched + (1 if lines_a[left] == lines_b[right] else 0)
        next_length = length + 1
        if next_length - next_matched > MISMATCH_RATIO * next_length:
            break
        matched, length = next_matched, next_length
        left += 1
        right += 1
    return start_a, length, matched


def _collect_clones(
    per_file: list[list[str]],
    votes: dict[tuple[int, int, int], list[tuple[int, int]]],
) -> list[tuple[float, int, int, int, int, int]]:
    """Every surviving alignment: (score, file_a, start_a, file_b, start_b, length).

    One alignment reports once no matter how many anchors voted for it -
    the alignment's best block wins, ties resolved by the earliest
    start, so one clone is one row. Same-file regions that overlap
    never accuse each other: a stride-one sweep shares most of its
    lines with its neighbor by construction, and self-overlap is not a
    clone.
    """
    clones: dict[tuple[int, int, int], tuple[float, int, int, int, int, int]] = {}
    for (file_a, file_b, delta), anchors in votes.items():
        if len(anchors) < VOTE_FLOOR:
            continue
        if file_a == file_b and -delta < MIN_MATCHED:
            continue  # overlapping self-regions, or the same region
        anchor_a, anchor_b = min(anchors)  # one anchor per alignment, stable
        start_a, length, matched = _grow_block(
            per_file[file_a], per_file[file_b], anchor_a, anchor_b
        )
        if matched < MIN_MATCHED:
            continue
        start_b = start_a - delta
        if file_a == file_b and start_b < start_a + length and start_a < start_b + length:
            continue
        score = matched / length
        if score < SIM_THRESHOLD:
            continue
        key = (file_a, file_b, delta)
        row = (score, file_a, start_a, file_b, start_b, length)
        if key not in clones or clones[key][0] < score:
            clones[key] = row
    return list(clones.values())


def check_near_duplicates(
    root: Path | None,
    files: list[Path],
    globs: list[str] | None = None,
) -> list[Finding]:
    """The near-duplicate clones, best similarity first, capped.

    ``root`` + ``globs`` honor ``.ai-integrity-ignore``: the repo's own
    fixtures carry deliberate near-clones (the benchmark's tree exists
    to fire this rule) and a detector must not judge the stage props it
    is pointed at. Direct callers that already scoped their file list
    pass the root as ``None``.
    """
    scoped = (
        files
        if root is None or globs is None
        else [path for path in files if not _ignored(path, root, globs)]
    )
    per_file: list[list[str]] = []
    linenos: list[list[int]] = []
    paths: list[str] = []
    for path in sorted(scoped):
        if path.suffix.lower() not in CODE_EXTS:
            continue
        lines, numbers = _normalized_lines(read_text_lax(path))
        if len(lines) < MIN_MATCHED:
            continue
        paths.append(str(path))
        per_file.append(lines)
        linenos.append(numbers)
    clones = _collect_clones(per_file, _votes(_index_anchors(per_file)))
    clones.sort(key=lambda row: (-row[0], paths[row[1]], row[2], paths[row[3]], row[4]))
    findings: list[Finding] = []
    for score, file_a, start_a, file_b, start_b, length in clones[:MAX_CLONES]:
        findings.append(
            Finding(
                path=paths[file_a],
                line=linenos[file_a][start_a],
                check="near-duplicate-clone",
                message=(
                    f"near-duplicate clone of {paths[file_b]}:{linenos[file_b][start_b]} "
                    f"(similarity {score:.2f} over {length} aligned lines)"
                ),
                weight=2,
            )
        )
    return findings
