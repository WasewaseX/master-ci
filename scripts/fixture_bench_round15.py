"""The round-15 fixture rows: the C# accuracy column deepening.

Data module for fixture_benchmark.PROBES - the tranche's slugs live
here so the runner module keeps its own 400-line budget. The tranche
pushes the eleven one-judged-row C# checks to the five-judged-row
promotion threshold, each check with four diverse real-shaped positives
and one nearest-negative clean row pinning the rule's exemption logic:
the injected logger, the handled-and-flagged catch, the switch
statement, the foreach loop, the .Any() guard, the real method body,
the explicit null check, the StringBuilder, the awaited call, the
justified suppression, and the informative summary.
"""

from __future__ import annotations

ROUND15_CSHARP_DETECTED = (
    # csharp-console-leftover: Console/Debug/Trace output in library code
    "csharp-console-service-diag",
    "csharp-console-trace-repository",
    "csharp-console-error-sweeper",
    "csharp-console-receipt-printer",
    # csharp-empty-catch-rethrow: catch whose body only rethrows
    "csharp-rethrow-parse-tier",
    "csharp-rethrow-io-tier",
    "csharp-rethrow-blank-tier",
    "csharp-rethrow-two-tiers",
    # csharp-if-ladder: four-plus branches comparing one subject
    "csharp-ifladder-shipping-tiers",
    "csharp-ifladder-order-status",
    "csharp-ifladder-file-mime",
    "csharp-ifladder-locale-map",
    # csharp-index-loop: index for-loop over Length/Count
    "csharp-index-tally-orders",
    "csharp-index-copy-samples",
    "csharp-index-grid-rows",
    "csharp-index-name-scan",
    # csharp-linq-count: .Count() answering a yes/no question
    "csharp-linq-empty-guard",
    "csharp-linq-has-items",
    "csharp-linq-under-limit",
    "csharp-linq-nonzero-queue",
    # csharp-not-implemented: NotImplementedException stub
    "csharp-notimpl-export-method",
    "csharp-notimpl-legacy-gateway",
    "csharp-notimpl-region-hint",
    "csharp-notimpl-audit-sink",
    # csharp-null-forgiving: null-forgiving ! silencing the warning
    "csharp-nullbang-config-section",
    "csharp-nullbang-registry-entry",
    "csharp-nullbang-cached-invoice",
    "csharp-nullbang-argument-guard",
    # csharp-string-concat-in-loop: += of a literal inside a loop body
    "csharp-concat-csv-rows",
    "csharp-concat-html-table",
    "csharp-concat-log-buffer",
    "csharp-concat-query-params",
    # csharp-sync-over-async: .Result / .Wait() / GetAwaiter().GetResult()
    "csharp-blocking-result-read",
    "csharp-blocking-wait-finish",
    "csharp-blocking-getresult-gate",
    "csharp-blocking-startup-warmup",
    # csharp-suppressed-warning: suppression without a stated reason
    "csharp-suppress-pragma-nullable",
    "csharp-suppress-pragma-obsolete",
    "csharp-suppress-message-analyzer",
    "csharp-suppress-message-performance",
    # csharp-redundant-doc-comment: summary restating the member
    "csharp-docrestate-customer-name",
    "csharp-docrestate-order-count",
    "csharp-docrestate-invoice-total",
    "csharp-docrestate-user-role",
)

ROUND15_CSHARP_RENAMED = {
    "csharp-console-service-diag": "csharp-console-leftover",
    "csharp-console-trace-repository": "csharp-console-leftover",
    "csharp-console-error-sweeper": "csharp-console-leftover",
    "csharp-console-receipt-printer": "csharp-console-leftover",
    "csharp-rethrow-parse-tier": "csharp-empty-catch-rethrow",
    "csharp-rethrow-io-tier": "csharp-empty-catch-rethrow",
    "csharp-rethrow-blank-tier": "csharp-empty-catch-rethrow",
    "csharp-rethrow-two-tiers": "csharp-empty-catch-rethrow",
    "csharp-ifladder-shipping-tiers": "csharp-if-ladder",
    "csharp-ifladder-order-status": "csharp-if-ladder",
    "csharp-ifladder-file-mime": "csharp-if-ladder",
    "csharp-ifladder-locale-map": "csharp-if-ladder",
    "csharp-index-tally-orders": "csharp-index-loop",
    "csharp-index-copy-samples": "csharp-index-loop",
    "csharp-index-grid-rows": "csharp-index-loop",
    "csharp-index-name-scan": "csharp-index-loop",
    "csharp-linq-empty-guard": "csharp-linq-count",
    "csharp-linq-has-items": "csharp-linq-count",
    "csharp-linq-under-limit": "csharp-linq-count",
    "csharp-linq-nonzero-queue": "csharp-linq-count",
    "csharp-notimpl-export-method": "csharp-not-implemented",
    "csharp-notimpl-legacy-gateway": "csharp-not-implemented",
    "csharp-notimpl-region-hint": "csharp-not-implemented",
    "csharp-notimpl-audit-sink": "csharp-not-implemented",
    "csharp-nullbang-config-section": "csharp-null-forgiving",
    "csharp-nullbang-registry-entry": "csharp-null-forgiving",
    "csharp-nullbang-cached-invoice": "csharp-null-forgiving",
    "csharp-nullbang-argument-guard": "csharp-null-forgiving",
    "csharp-concat-csv-rows": "csharp-string-concat-in-loop",
    "csharp-concat-html-table": "csharp-string-concat-in-loop",
    "csharp-concat-log-buffer": "csharp-string-concat-in-loop",
    "csharp-concat-query-params": "csharp-string-concat-in-loop",
    "csharp-blocking-result-read": "csharp-sync-over-async",
    "csharp-blocking-wait-finish": "csharp-sync-over-async",
    "csharp-blocking-getresult-gate": "csharp-sync-over-async",
    "csharp-blocking-startup-warmup": "csharp-sync-over-async",
    "csharp-suppress-pragma-nullable": "csharp-suppressed-warning",
    "csharp-suppress-pragma-obsolete": "csharp-suppressed-warning",
    "csharp-suppress-message-analyzer": "csharp-suppressed-warning",
    "csharp-suppress-message-performance": "csharp-suppressed-warning",
    "csharp-docrestate-customer-name": "csharp-redundant-doc-comment",
    "csharp-docrestate-order-count": "csharp-redundant-doc-comment",
    "csharp-docrestate-invoice-total": "csharp-redundant-doc-comment",
    "csharp-docrestate-user-role": "csharp-redundant-doc-comment",
}

ROUND15_CSHARP_CLEAN_RULES = {
    "csharp-console-logger-clean": "csharp-console-leftover",
    "csharp-rethrow-handled-clean": "csharp-empty-catch-rethrow",
    "csharp-ifladder-switch-clean": "csharp-if-ladder",
    "csharp-index-foreach-clean": "csharp-index-loop",
    "csharp-linq-any-clean": "csharp-linq-count",
    "csharp-notimpl-real-body-clean": "csharp-not-implemented",
    "csharp-nullbang-check-clean": "csharp-null-forgiving",
    "csharp-concat-builder-clean": "csharp-string-concat-in-loop",
    "csharp-blocking-await-clean": "csharp-sync-over-async",
    "csharp-suppress-justified-clean": "csharp-suppressed-warning",
    "csharp-docrestate-informative-clean": "csharp-redundant-doc-comment",
}
