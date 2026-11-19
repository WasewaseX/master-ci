"""The vertical slice's benchmark: performance, quality, and the delta.

    Three phases, one command, JSON on demand:

    1. Performance - lower this repository's own Python, TypeScript and
        Go surface to IR and report wall time, per-parser split and
        corpus counts. This is the measurement harness the architecture
        decision record points at: if the Python IR builder ever
        dominates the scan budget, that number - not taste - is what
        justifies moving the hot loop into a compiled engine.

    2. Detection quality on the labeled corpus - the flawed_python
        positive control and the clean_* negative controls yield TP/FP/
        FN, precision and recall. A rule with insufficient samples stays
        advisory, and no promotion is claimed from unit tests alone.

    3. The delta against the textual occurrence oracle - the same
        corpus under slop_dead's textual deadness check, so the
        semantic slice's contribution is visible as concrete findings
        the prose-kept-alive names escaped, and nothing the textual
        oracle caught was lost.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from slop_dead import check_unused_declarations
from slop_symbol_graph import build_symbol_graph, check_semantic_dead_symbols

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("scripts", "tests")
CORPUS = ROOT / "benchmarks" / "accuracy"
FLAWED = ("flawed_python",)


def _py_files() -> list[Path]:
    files: list[Path] = []
    for name in SCAN_DIRS:
        base = ROOT / name
        files.extend(p for p in sorted(base.rglob("*.py")) if p.is_file())
    return files


def phase_performance() -> dict:
    """Lower the repo's own surface to IR and time it."""
    files = [p for p in _py_files() if ".venv" not in p.parts and "__pycache__" not in p.parts]
    start = time.perf_counter()
    graph = build_symbol_graph(ROOT, files, [])
    elapsed = time.perf_counter() - start
    symbols = sum(len(ir.symbols) for ir in graph.files.values())
    identifiers = sum(len(ir.identifiers) for ir in graph.files.values())
    calls = sum(len(ir.calls) for ir in graph.files.values())
    imports = sum(len(ir.imports) for ir in graph.files.values())
    return {
        "files_lowered": len(graph.files),
        "wall_seconds": round(elapsed, 3),
        "files_per_second": round(len(graph.files) / elapsed, 1) if elapsed else 0,
        "symbols": symbols,
        "identifiers": identifiers,
        "calls": calls,
        "imports": imports,
        "parse_failures": len(graph.parse_failures),
    }


def _flawed_rows() -> tuple[int, int, int, list[dict]]:
    """TP/FP/positive counts and details over the flawed corpora."""
    tp, fp, positives = 0, 0, 0
    details: list[dict] = []
    for name in FLAWED:
        base = CORPUS / name
        files = sorted(p for p in base.rglob("*.py") if p.is_file())
        findings = check_semantic_dead_symbols(base, files, [])
        hits = [f for f in findings if "_audit_helper_only" in f.message]
        positives += 1
        tp += len(hits)
        fp += len(findings) - len(hits)
        details.append({"corpus": name, "findings": [f.message for f in findings]})
    return tp, fp, positives, details


def _control_findings() -> dict[str, int]:
    """Finding counts over every clean_* negative control."""
    controls: dict[str, int] = {}
    for entry in sorted(CORPUS.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("clean_"):
            continue
        suffixes = (".py", ".ts", ".js", ".go")
        files = [p for p in sorted(entry.rglob("*")) if p.is_file() and p.suffix in suffixes]
        controls[entry.name] = len(check_semantic_dead_symbols(entry, files, []))
    return controls


def phase_quality() -> dict:
    """TP/FP/FN on the labeled corpus, precision and recall."""
    tp, fp, positives, details = _flawed_rows()
    controls = _control_findings()
    fp += sum(controls.values())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / positives if positives else 0.0
    return {
        "labeled_positives": positives,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": positives - tp,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "clean_control_findings": controls,
        "flawed_details": details,
    }


def _summary(findings) -> list[str]:
    """'check name' rows so the two oracles' split is legible."""
    rows: list[str] = []
    for f in findings:
        name = f.message.split("'")[1] if "'" in f.message else f.message
        rows.append(f"{f.check}: {name}")
    return rows


def phase_delta() -> dict:
    """Semantic findings vs the textual occurrence oracle, same corpus."""
    rows: list[dict] = []
    for name in FLAWED:
        base = CORPUS / name
        files = sorted(p for p in base.rglob("*.py") if p.is_file())
        semantic = check_semantic_dead_symbols(base, files, [])
        textual = check_unused_declarations(base, files, [])
        semantic_names = {s.split(": ")[1] for s in _summary(semantic)}
        textual_names = {s.split(": ")[1] for s in _summary(textual)}
        rows.append(
            {
                "corpus": name,
                "semantic": _summary(semantic),
                "textual": _summary(textual),
                "semantic_only": sorted(semantic_names - textual_names),
                "textual_only": sorted(textual_names - semantic_names),
            }
        )
    return {"corpora": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="benchmark the IR vertical slice")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()
    report = {
        "performance": phase_performance(),
        "quality": phase_quality(),
        "delta_vs_textual": phase_delta(),
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        perf = report["performance"]
        qual = report["quality"]
        print(
            f"performance: {perf['files_lowered']} files in {perf['wall_seconds']}s "
            f"({perf['files_per_second']}/s), {perf['symbols']} symbols, "
            f"{perf['identifiers']} identifiers, {perf['parse_failures']} parse failures"
        )
        print(
            f"quality: TP {qual['true_positives']}, FP {qual['false_positives']}, "
            f"FN {qual['false_negatives']}, precision {qual['precision']}, "
            f"recall {qual['recall']}"
        )
        for corpus, count in sorted(qual["clean_control_findings"].items()):
            print(f"  control {corpus}: {count} findings")
        for row in report["delta_vs_textual"]["corpora"]:
            print(
                f"delta on {row['corpus']}: semantic {len(row['semantic_findings'])}, "
                f"textual {len(row['textual_findings'])}"
            )


if __name__ == "__main__":
    main()
