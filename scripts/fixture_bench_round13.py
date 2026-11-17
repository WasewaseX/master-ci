"""The round-13 fixture rows: the C/C++ accuracy column deepening.

Data module for fixture_benchmark.PROBES - the tranche's slugs live
here so the runner module keeps its own 400-line budget. The tranche
pushes the whole eight-check C/C++ column to the five-judged-row
promotion threshold, each check with four diverse real-shaped positives
and one nearest-negative clean row pinning the rule's exemption logic:
the named casts, the constexpr constant, the newline literal, the
main()-owning CLI, the unique_ptr owner, the real error throw, the
nullptr boundary call, and the translation-unit directive.
"""

from __future__ import annotations

ROUND13_CPP_DETECTED = (
    "cpp-cast-driver-cast",
    "cpp-cast-db-pointer",
    "cpp-cast-ratio-division",
    "cpp-cast-byte-length",
    "cpp-define-timeout-ms",
    "cpp-define-default-port",
    "cpp-define-oversample",
    "cpp-define-local-path-max",
    "cpp-endl-logger-flush",
    "cpp-endl-progress-line",
    "cpp-endl-report-row",
    "cpp-endl-banner-loop",
    "cpp-iostream-debug-trace",
    "cpp-iostream-inspect-cout",
    "cpp-iostream-cerr-parser",
    "cpp-iostream-two-sites",
    "cpp-delete-cache-raw",
    "cpp-delete-buffer-parse",
    "cpp-delete-plugin-release",
    "cpp-delete-two-resources",
    "cpp-notimpl-interface-stub",
    "cpp-notimpl-platform-gap",
    "cpp-notimpl-assert-stub",
    "cpp-notimpl-optional-cap",
    "cpp-null-legacy-call",
    "cpp-null-member-init",
    "cpp-null-handle-reset",
    "cpp-null-default-arg",
    "cpp-usingns-widget-header",
    "cpp-usingns-util-header",
    "cpp-usingns-logger-header",
    "cpp-usingns-geometry-header",
)

ROUND13_CPP_RENAMED = {
    "cpp-cast-driver-cast": "cpp-c-style-cast",
    "cpp-cast-db-pointer": "cpp-c-style-cast",
    "cpp-cast-ratio-division": "cpp-c-style-cast",
    "cpp-cast-byte-length": "cpp-c-style-cast",
    "cpp-define-timeout-ms": "cpp-define-constant",
    "cpp-define-default-port": "cpp-define-constant",
    "cpp-define-oversample": "cpp-define-constant",
    "cpp-define-local-path-max": "cpp-define-constant",
    "cpp-endl-logger-flush": "cpp-endl-in-stream",
    "cpp-endl-progress-line": "cpp-endl-in-stream",
    "cpp-endl-report-row": "cpp-endl-in-stream",
    "cpp-endl-banner-loop": "cpp-endl-in-stream",
    "cpp-iostream-debug-trace": "cpp-iostream-leftover",
    "cpp-iostream-inspect-cout": "cpp-iostream-leftover",
    "cpp-iostream-cerr-parser": "cpp-iostream-leftover",
    "cpp-iostream-two-sites": "cpp-iostream-leftover",
    "cpp-delete-cache-raw": "cpp-manual-delete",
    "cpp-delete-buffer-parse": "cpp-manual-delete",
    "cpp-delete-plugin-release": "cpp-manual-delete",
    "cpp-delete-two-resources": "cpp-manual-delete",
    "cpp-notimpl-interface-stub": "cpp-not-implemented",
    "cpp-notimpl-platform-gap": "cpp-not-implemented",
    "cpp-notimpl-assert-stub": "cpp-not-implemented",
    "cpp-notimpl-optional-cap": "cpp-not-implemented",
    "cpp-null-legacy-call": "cpp-null-literal",
    "cpp-null-member-init": "cpp-null-literal",
    "cpp-null-handle-reset": "cpp-null-literal",
    "cpp-null-default-arg": "cpp-null-literal",
    "cpp-usingns-widget-header": "cpp-using-namespace-std-in-header",
    "cpp-usingns-util-header": "cpp-using-namespace-std-in-header",
    "cpp-usingns-logger-header": "cpp-using-namespace-std-in-header",
    "cpp-usingns-geometry-header": "cpp-using-namespace-std-in-header",
}

ROUND13_CPP_CLEAN_RULES = {
    "cpp-cast-named-casts-clean": "cpp-c-style-cast",
    "cpp-define-constexpr-clean": "cpp-define-constant",
    "cpp-endl-newline-clean": "cpp-endl-in-stream",
    "cpp-iostream-cli-main-clean": "cpp-iostream-leftover",
    "cpp-delete-unique-ptr-clean": "cpp-manual-delete",
    "cpp-notimpl-real-throw-clean": "cpp-not-implemented",
    "cpp-null-nullptr-clean": "cpp-null-literal",
    "cpp-usingns-tu-clean": "cpp-using-namespace-std-in-header",
}
