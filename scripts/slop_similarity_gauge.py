"""The prefix-gauge fast path for the near-duplicate similarity oracle.

Growing one anchor pair is an integer random walk - a match steps -1,
a mismatch +3, alive inside its budget - so prefixing the diagonal
once as ``gauge[v] = 4 x misses[v] - v`` turns every anchor's stop
into a range query over the gauge: the same stop the walk finds, from
``log^2`` sparse-table lookups instead of re-walking the same matched
lines once per anchor. The routing decides WHEN that trade pays (see
``slop_similarity._best_alignment_block``); this module owns the HOW -
the row kernel, the tables, the two stop queries and the query-path
best block, which must be the identical block the direct walk picks
(the equivalence is test-pinned in ``tests/test_similarity.py`` on
seeded random diagonals of every shape).
"""

from __future__ import annotations

SIM_THRESHOLD = 0.7
MIN_MATCHED = 8


def _qualifying_row(
    file_a: int, file_b: int, delta: int, start_a: int, length: int, matched: int
) -> tuple[float, int, int, int, int, int] | None:
    """The reported row for one grown block, or None when it does not qualify.

    The mismatch budget is guaranteed by the walk itself - the ratio
    caps mismatches at a quarter of the block - so the filters here
    are the match floor, the same-file overlap rule and the threshold,
    kept explicit rather than assumed.
    """
    if matched < MIN_MATCHED:
        return None
    start_b = start_a - delta
    if file_a == file_b and start_b < start_a + length and start_a < start_b + length:
        return None  # overlapping self-regions, or the same region
    score = matched / length
    if score < SIM_THRESHOLD:
        return None
    return (score, file_a, start_a, file_b, start_b, length)


def _best_row(
    rows: list[tuple[int, float, int, tuple[float, int, int, int, int, int]]],
) -> tuple[float, int, int, int, int, int] | None:
    """Most matched evidence first, then score, ties by the earliest start."""
    if not rows:
        return None
    return max(rows)[-1]


def _sparse_extremes(values: list[int]) -> tuple[list[list[int]], list[list[int]]]:
    """(min, max) sparse tables over ``values``; any slice's extreme is O(1).

    ``table[k][i]`` holds the extreme of ``values[i : i + 2**k]``, so a
    slice is one overlap of two power-of-two windows; the stop queries
    binary-search this predicate, which turns a per-anchor walk into a
    log-squared lookup.
    """
    size = len(values)
    levels = size.bit_length()
    mins: list[list[int]] = [values]
    maxs: list[list[int]] = [values]
    for k in range(1, levels):
        half = 1 << (k - 1)
        width = size - (1 << k) + 1
        mins.append([min(mins[k - 1][i], mins[k - 1][i + half]) for i in range(width)])
        maxs.append([max(maxs[k - 1][i], maxs[k - 1][i + half]) for i in range(width)])
    return mins, maxs


def _range_extreme(table: list[list[int]], start: int, end: int, bigger: bool) -> int:
    """The slice's min (``bigger=False``) or max (``bigger=True``), O(1)."""
    k = (end - start).bit_length() - 1
    row = table[k]
    left, right = row[start], row[end - (1 << k)]
    return max(left, right) if bigger else min(left, right)


def _last_under(table: list[list[int]], start: int, end: int, limit: int) -> int | None:
    """The largest index in [start, end) whose value is under ``limit``."""
    if start >= end or _range_extreme(table, start, end, False) >= limit:
        return None
    low, high = start, end - 1
    while low < high:
        mid = (low + high + 1) // 2
        if _range_extreme(table, mid, end, False) < limit:
            low = mid
        else:
            high = mid - 1
    return low


def _first_over(table: list[list[int]], start: int, end: int, limit: int) -> int | None:
    """The smallest index in [start, end) whose value is over ``limit``."""
    if start >= end or _range_extreme(table, start, end, True) <= limit:
        return None
    low, high = start, end - 1
    while low < high:
        mid = (low + high) // 2
        if _range_extreme(table, start, mid + 1, True) > limit:
            high = mid
        else:
            low = mid + 1
    return low


def _diagonal_gauge(
    lines_a: list[str], lines_b: list[str], delta: int
) -> tuple[int, int, list[int], list[int]]:
    """One alignment's diagonal: (low, span, misses, gauge).

    ``misses[v]`` counts mismatched line pairs before ``low + v``;
    ``gauge[v] = 4 x misses[v] - v`` is the walk's budget in integer
    form - a match steps -1, a mismatch +3, alive inside its budget.
    """
    low = max(0, delta)
    high = min(len(lines_a), len(lines_b) + delta)
    span = high - low
    misses = [0] * (span + 1)
    for v in range(span):
        misses[v + 1] = misses[v] + (0 if lines_a[low + v] == lines_b[low + v - delta] else 1)
    return low, span, misses, [4 * misses[v] - v for v in range(span + 1)]


def _left_stop(mins: list[list[int]], gauge: list[int], v_anchor: int) -> int:
    """The walk's left break, as a gauge index; the block starts above it.

    Absorbing the k-th line below the anchor keeps the block iff
    ``4 x mismatches - k <= 2``; prefixed, that is ``gauge[i] >=
    gauge[anchor] - 2`` - one range-min query over the gauge.
    """
    if v_anchor == 0:
        return 0
    below = _last_under(mins, 0, v_anchor, gauge[v_anchor] - 2)
    return 0 if below is None else below + 1


def _right_stop(
    maxs: list[list[int]], gauge: list[int], v_start: int, v_anchor: int, span: int
) -> int:
    """The walk's right break, exclusive, as a gauge index.

    The whole-block budget check - ``4 x misses over the block > block
    length`` - reads ``gauge[e+1] > gauge[block start]``, so the stop
    is the first such index past the anchor pair - one range-max query.
    """
    if v_anchor + 2 >= span:
        return span
    over = _first_over(maxs, v_anchor + 3, span + 1, gauge[v_start])
    return span if over is None else over - 1


def _best_block_by_queries(
    lines_a: list[str],
    lines_b: list[str],
    delta: int,
    positions: list[int],
    file_a: int,
    file_b: int,
) -> tuple[float, int, int, int, int, int] | None:
    """The identical best block, computed by queries instead of re-walks.

    ``_grow_block``'s walk along one diagonal is an integer random walk
    (match -1, mismatch +3, alive while the counter stays inside its
    budget), so prefixing the diagonal once as ``gauge[v] = 4 x misses
    - v`` turns every anchor's stop into a range query - the same stop
    the walk finds, without walking the same matched lines once per
    anchor line. Re-walking made a 4,000-line near-copy quadratic
    (~1.8s); the queries keep the best block and drop the repetition.
    """
    low, span, misses, gauge = _diagonal_gauge(lines_a, lines_b, delta)
    mins, maxs = _sparse_extremes(gauge)
    rows: list[tuple[int, float, int, tuple[float, int, int, int, int, int]]] = []
    for anchor in positions:
        v_anchor = anchor - low
        v_start = _left_stop(mins, gauge, v_anchor)
        v_end = _right_stop(maxs, gauge, v_start, v_anchor, span)
        length = v_end - v_start
        matched = length - (misses[v_end] - misses[v_start])
        start_a = low + v_start
        row = _qualifying_row(file_a, file_b, delta, start_a, length, matched)
        if row is not None:
            rows.append((matched, row[0], -start_a, row))
    return _best_row(rows)
