"""Rate rounding tests (fixture: three exact-duplicate bodies, one distinct)."""


def test_rounded_rate_matches_reference():
    assert round(1.085, 2) == 1.09


def test_rounded_rate_matches_reference_again():
    assert round(1.085, 2) == 1.09


def test_rounded_rate_matches_reference_repeated():
    assert round(1.085, 2) == 1.09


def test_line_sum_matches_invoice_total():
    lines = [1.25, 2.5, 3.75]
    assert sum(lines) == 7.5
