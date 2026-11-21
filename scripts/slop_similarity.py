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
- a passing alignment keeps its per-anchor semantics at a linear-ish
    cost: every anchor grows outward while the mismatch ratio holds -
    a short shared preamble voting for a delta must not hide a much
    longer copied block at the same offset, so the alignment's best
    block wins - and the walk is reused instead of repeated: growing
    one anchor pair is an integer random walk, so prefixing the
    diagonal once turns every anchor's stop into a range query over
    ``4 x misses - offset``. Which path an alignment takes is decided
    by estimated work, not by anchor count alone: the gauge tables
    cover the whole diagonal, so a short shared helper inside long
    otherwise-unrelated files (19 anchors over a 4,000-line span)
    would pay 96,000 table cells for walks it finishes in ~400
    comparisons - three sampled walks measure the alignment's actual
    block length, and only an alignment whose sampled cost outweighs
    the tables (a long near-copy, one anchor per copied line) takes
    the gauge. Re-walking made that 4,000-line near-copy cost ~1.8s;
    the query path keeps the identical best block for a fraction of
    that - and sparse alignments keep the direct walk. The score is
    the evidence: the
    message names both locations and the similarity, so a review
    starts from a number, not a vibe.

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
from slop_similarity_gauge import (  # noqa: F401 - the row kernel lives beside the gauge queries
    MIN_MATCHED,
    SIM_THRESHOLD,
    _best_block_by_queries,
    _best_row,
    _qualifying_row,
)

MISMATCH_RATIO = 0.25
VOTE_FLOOR = 5
MAX_OWNERS = 24
MAX_CLONES = 3

# Alignments with more anchors than this are candidates for the prefix
# gauge; the routing decision itself is estimated work (see
# ``_best_alignment_block``) - the count only skips the sampling for
# alignments that are cheap to walk regardless of their span.
_DIRECT_WALK_ANCHORS = 16

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


def _query_table_work(span: int) -> int:
    """The gauge path's work: two extreme tables, one level per doubling.

    ``_sparse_extremes`` builds ``mins`` and ``maxs`` over ``span``
    entries, ``ceil(log2(span))`` levels each - the cost every anchor's
    two range queries amortize against.
    """
    return 2 * span * max(1, span.bit_length())


def _sampled_walk_work(
    positions: list[int], lines_a: list[str], lines_b: list[str], delta: int
) -> int:
    """The direct walks' measured cost: three spread anchors, scaled.

    A walk's cost is the block it grows, and an alignment's blocks live
    around its anchors - so the longest block among the first, middle
    and last anchor (the spread covers a cluster at either end) times
    the anchor count estimates the full routing's cost without walking
    every anchor. A 19-anchor helper shared inside 4,000-line files
    samples ~20-line blocks: 380 comparisons, far under its 96,000
    table cells - the gauge would price the whole diagonal for an
    alignment the walks finish near the helper. A 4,000-line near-copy
    samples a 4,000-line block: the estimate clears the tables and the
    gauge earns its prefix pass.
    """
    picks = sorted({positions[0], positions[len(positions) // 2], positions[-1]})
    longest = max(_grow_block(lines_a, lines_b, p, p - delta)[1] for p in picks)
    return longest * len(positions)


def _best_alignment_block(
    per_file: list[list[str]],
    file_a: int,
    file_b: int,
    delta: int,
    anchors: list[tuple[int, int]],
) -> tuple[float, int, int, int, int, int] | None:
    """Grow every anchor of one alignment; the best block wins.

    Most matched evidence first, then the highest similarity, ties
    resolved by the earliest start - a short shared preamble (an early
    anchor whose growth stops at the first differing line) cannot hide
    a much longer near-copy at the same delta whose anchors voted
    later, and a tiny perfect sub-block cannot crowd out the longer
    copy it lives inside. A candidate that misses the match floor,
    overlaps its sibling in the same file or clears the similarity
    threshold is not a candidate.

    The growths reuse the walk instead of repeating it - and the path
    is chosen by estimated work, not by anchor count alone: a count
    gate alone priced a 19-anchor shared helper inside 4,000-line
    files with gauge tables over the whole diagonal (276 such
    alignments cost 2.34s where the walks take 0.016s), because the
    tables cover every line of the overlap while the walks stop near
    the helper. Three sampled walks measure the alignment's actual
    block length; only an alignment whose sampled cost outweighs the
    tables it would build (a long near-copy, one anchor per copied
    line) answers its stops from the prefixed gauge
    (``_best_block_by_queries``), the identical block the walk would
    find. Re-walking every anchor made two 4,000-line near-copies cost
    about 1.8 seconds (the work quadruples with their length); the
    query path keeps the semantics and drops the repetition.
    """
    lines_a, lines_b = per_file[file_a], per_file[file_b]
    positions = sorted({anchor_a for anchor_a, _ in anchors})
    if len(positions) > _DIRECT_WALK_ANCHORS:
        low = max(0, delta)
        span = min(len(lines_a), len(lines_b) + delta) - low
        if span > 0 and _sampled_walk_work(positions, lines_a, lines_b, delta) > _query_table_work(
            span
        ):
            return _best_block_by_queries(lines_a, lines_b, delta, positions, file_a, file_b)
    rows: list[tuple[int, float, int, tuple[float, int, int, int, int, int]]] = []
    for anchor_a in positions:
        start_a, length, matched = _grow_block(lines_a, lines_b, anchor_a, anchor_a - delta)
        row = _qualifying_row(file_a, file_b, delta, start_a, length, matched)
        if row is not None:
            rows.append((matched, row[0], -start_a, row))
    return _best_row(rows)


def _collect_clones(
    per_file: list[list[str]],
    votes: dict[tuple[int, int, int], list[tuple[int, int]]],
) -> list[tuple[float, int, int, int, int, int]]:
    """Every surviving alignment: (score, file_a, start_a, file_b, start_b, length).

    One alignment reports once no matter how many anchors voted for it,
    but every anchor gets its chance to grow (``_best_alignment_block``),
    so the alignment's best block is the one reported - with the
    stops answered from the prefix gauge when the anchor vote is
    dense, so the per-anchor chance never costs a per-anchor walk.
    Same-file
    alignments whose delta overlaps by construction stay out entirely:
    a stride-one sweep shares most of its lines with its neighbor by
    construction, and self-overlap is not a clone.
    """
    clones: dict[tuple[int, int, int], tuple[float, int, int, int, int, int]] = {}
    for (file_a, file_b, delta), anchors in votes.items():
        if len(anchors) < VOTE_FLOOR:
            continue
        if file_a == file_b and -delta < MIN_MATCHED:
            continue  # overlapping self-regions, or the same region
        best_row = _best_alignment_block(per_file, file_a, file_b, delta, anchors)
        if best_row is not None:
            clones[(file_a, file_b, delta)] = best_row
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
