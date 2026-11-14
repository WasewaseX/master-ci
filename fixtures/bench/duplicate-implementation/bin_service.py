"""Reconcile bin units against the ledger floor."""


def reconcile_units(records, ledger):
    staged = prepare_stage(records, ledger.floor)
    counted = tally_units(staged, ledger.rate)
    variance = counted - ledger.expected_units
    tolerance = ledger.tolerance_for_records(records)
    if abs(variance) > tolerance:
        flagged = flag_variance(variance, tolerance)
        ledger.record_variance(flagged, variance)
        ledger.commit_stage(staged, counted, variance)
        return escalate_variance(staged, variance, ledger)
    ledger.commit_units(counted, variance, ledger.cycle)
    ledger.mark_reconciled(staged, counted, variance)
    return close_stage(staged, counted, variance)
