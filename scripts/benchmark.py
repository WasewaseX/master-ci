"""Master CI performance benchmark.

Measures the throughput and latency of the four core engines against a
deterministic synthetic corpus: the AI-integrity detector, the fix
engine (plan and generic scan), the path filter translator, and the MCP
server (process startup plus JSON-RPC roundtrips over stdio). Results
print as a markdown table and can be written as JSON with --json or
--save, then compared against a saved baseline with --check to catch
performance regressions before they ship.

The corpus is generated with a fixed seed, so findings counts and file
counts are identical across runs and machines; only timings vary. Run
it with --check in CI or locally before tagging a release.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

SEED = 20261106
# scale name -> (python_files, ts_files, go_files, markdown_files)
SCALES: dict[str, tuple[int, int, int, int]] = {
    "small": (8, 8, 4, 4),
    "full": (120, 120, 60, 60),
}

SLOP_PHRASES = (
    "delve into the details",
    "it is important to note that",
    "in today's fast-paced world",
    "leverage the power of",
    "seamlessly integrate",
    "cutting-edge technology",
    "robust and scalable solution",
    "unlock the full potential",
)

# ---------------------------------------------------------------------------
# Deterministic synthetic corpus
# ---------------------------------------------------------------------------


def _py_source(i: int, rng: random.Random) -> str:
    slop = rng.choice(SLOP_PHRASES) if i % 4 == 0 else "parse the input payload"
    funcs = "\n\n".join(
        f"def handle_{i}_{k}(value: int) -> int:\n"
        f"    # TODO: tighten the loop bounds for case {k}\n"
        f"    total = 0\n"
        f"    for step in range(value % 50):\n"
        f"        if step % {k + 2} == 0:\n"
        f"            total += step * {k + 1}\n"
        f"        elif step % 3 == 0:\n"
        f"            total -= 1\n"
        f"    return total\n"
        for k in range(4)
    )
    stub = "\n\n    ...\n" if i % 9 == 0 else ""
    return (
        f'"""Module {i} of the synthetic benchmark corpus."""\n'
        f"import json\n\n\n"
        f"class Handler{i}:\n"
        f'    """Comment controller for payload family {i}."""\n\n'
        f"    def __init__(self) -> None:\n"
        f"        self.count = 0\n\n"
        f"{funcs}{stub}\n\n"
        f"def load(i: int) -> str:\n"
        f"    # {slop}\n"
        f'    return json.dumps({{"i": i, "kind": "py"}})\n'
    )


def _ts_source(i: int, rng: random.Random) -> str:
    slop = rng.choice(SLOP_PHRASES) if i % 3 == 0 else "map the request fields"
    body = "\n".join(
        f"export function step{i}_{k}(n: number): number {{\n"
        f"  // TODO: handle the empty case for {k}\n"
        f"  let acc = 0;\n"
        f"  for (let j = 0; j < n % 40; j++) {{\n"
        f"    acc += j * {k + 1};\n"
        f"  }}\n"
        f"  return acc;\n"
        f"}}\n"
        for k in range(3)
    )
    debug = "\nconsole.log('debug trace " + str(i) + "');\n" if i % 7 == 0 else ""
    return f"// {slop}\n{body}{debug}"


def _go_source(i: int, rng: random.Random) -> str:
    _ = rng.randint(0, 3)
    funcs = "\n\n".join(
        f"func Step{i}_{k}(n int) int {{\n"
        f"\tacc := 0\n"
        f"\tfor j := 0; j < n%{30 + k}; j++ {{\n"
        f"\t\tacc += j * {k + 2}\n"
        f"\t}}\n"
        f"\treturn acc\n"
        f"}}"
        for k in range(3)
    )
    ignored = '\n\nfunc Load(i int) string {\n\t_ = i\n\treturn "go"\n}' if i % 5 == 0 else ""
    return f"package bench{i % 7}\n\n{funcs}{ignored}\n"


def _md_source(i: int, rng: random.Random) -> str:
    slop = rng.choice(SLOP_PHRASES)
    body = (
        f"# Guide {i}\n\n"
        f"In today's fast-paced world, teams {slop}. It is important to note "
        f"that results may vary. \n\n"
        f"- first point about setup {i}\n- second point about usage\n- third point\n\n"
        f"## Details {i}\n\n" + ("Paragraph text with enough length to matter. " * 12)
    )
    return body + ("🎨 emoji heading\n" if i % 6 == 0 else "")


def build_corpus(root: Path, scale: str) -> dict[str, int]:
    """Write the deterministic corpus; returns file and byte counts."""
    py_n, ts_n, go_n, md_n = SCALES[scale]
    rng = random.Random(SEED)
    counts = {"files": 0, "bytes": 0}
    plan = [
        ("python", py_n, ".py", _py_source),
        ("typescript", ts_n, ".ts", _ts_source),
        ("go", go_n, ".go", _go_source),
        ("docs", md_n, ".md", _md_source),
    ]
    for subdir, count, suffix, maker in plan:
        target = root / subdir
        target.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            text = maker(i, rng)
            path = target / f"bench_{i:04d}{suffix}"
            path.write_text(text, encoding="utf-8")
            counts["files"] += 1
            counts["bytes"] += len(text.encode("utf-8"))
    return counts


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


def _best_of(repeat: int, fn: Callable[[], Any]) -> tuple[float, Any]:
    """Run fn repeat times; return (best wall seconds, last result)."""
    best = float("inf")
    result: Any = None
    for _ in range(max(1, repeat)):
        start = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - start)
    return best, result


def bench_detector(root: Path, repeat: int, counts: dict[str, int]) -> dict[str, Any]:
    import ai_slop_detector as detector

    phrases = detector.load_phrases()
    seconds, findings = _best_of(repeat, lambda: detector.scan(root, phrases))
    return {
        "detector_ms": seconds * 1000.0,
        "detector_files_per_sec": counts["files"] / seconds if seconds else 0.0,
        "detector_mb_per_sec": counts["bytes"] / 1e6 / seconds if seconds else 0.0,
        "detector_findings": float(len(findings)),
    }


def bench_fix_engine(root: Path, repeat: int) -> dict[str, Any]:
    import fix_engine

    plan_seconds, plan = _best_of(
        repeat, lambda: fix_engine.build_plan(root, {"safe", "lint"}, "p/default")
    )
    scan_seconds, scanned = _best_of(repeat, lambda: fix_engine.scan_generic(root, True))
    return {
        "fix_plan_ms": plan_seconds * 1000.0,
        "fix_plan_tools": float(len(plan)),
        "fix_generic_scan_ms": scan_seconds * 1000.0,
        "fix_generic_files": float(scanned.get("files_scanned", 0)),
    }


def bench_path_filters(repeat: int) -> dict[str, Any]:
    import path_filters

    filters = path_filters.load_filters(REPO / ".github" / "workflows" / "master-ci.yml")
    paths = [f"src/module_{i % 20}/file_{i}.ts" for i in range(2000)]
    paths += [f"pkg/store_{i % 10}/model_{i}.go" for i in range(1000)]
    seconds, fired = _best_of(repeat, lambda: path_filters.stacks_for_paths(paths, filters))
    return {
        "pathfilter_ms": seconds * 1000.0,
        "pathfilter_paths_per_sec": len(paths) / seconds if seconds else 0.0,
        "pathfilter_stacks": float(len(fired)),
    }


def bench_mcp(repeat: int, calls: int) -> dict[str, Any]:
    import mcp_ping

    server = REPO / "scripts" / "mcp_server.py"
    start = time.perf_counter()
    client = mcp_ping.Client(server, timeout=60.0)
    try:
        client.request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "benchmark", "version": "0"},
                },
            }
        )
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        client.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        startup = time.perf_counter() - start
        files = [f"src/module_{i}/file_{i}.ts" for i in range(10)]
        samples: list[float] = []
        for n in range(max(5, calls)):
            mark = time.perf_counter()
            client.request(
                {
                    "jsonrpc": "2.0",
                    "id": 100 + n,
                    "method": "tools/call",
                    "params": {"name": "filter_paths", "arguments": {"files": files}},
                }
            )
            samples.append(time.perf_counter() - mark)
        return {
            "mcp_startup_ms": startup * 1000.0,
            "mcp_roundtrip_ms": statistics.median(samples) * 1000.0,
        }
    finally:
        client.close()


# Higher-is-better rates get direction "up"; wall times get "down".
DIRECTIONS = {
    "detector_ms": "down",
    "detector_files_per_sec": "up",
    "detector_mb_per_sec": "up",
    "detector_findings": "info",
    "fix_plan_ms": "down",
    "fix_plan_tools": "info",
    "fix_generic_scan_ms": "down",
    "fix_generic_files": "info",
    "pathfilter_ms": "down",
    "pathfilter_paths_per_sec": "up",
    "pathfilter_stacks": "info",
    "mcp_startup_ms": "down",
    "mcp_roundtrip_ms": "down",
}

UNITS = {
    "detector_ms": "ms",
    "detector_files_per_sec": "files/s",
    "detector_mb_per_sec": "MB/s",
    "detector_findings": "count",
    "fix_plan_ms": "ms",
    "fix_plan_tools": "count",
    "fix_generic_scan_ms": "ms",
    "fix_generic_files": "count",
    "pathfilter_ms": "ms",
    "pathfilter_paths_per_sec": "paths/s",
    "pathfilter_stacks": "count",
    "mcp_startup_ms": "ms",
    "mcp_roundtrip_ms": "ms",
}


def run_suite(scale: str = "full", repeat: int = 3, with_mcp: bool = True) -> dict[str, float]:
    """Build the corpus and run every benchmark; returns metric -> value."""
    with tempfile.TemporaryDirectory(prefix="master-ci-bench-") as tmp:
        root = Path(tmp)
        counts = build_corpus(root, scale)
        metrics: dict[str, float] = {}
        metrics.update(bench_detector(root, repeat, counts))
        metrics.update(bench_fix_engine(root, repeat))
        metrics.update(bench_path_filters(repeat))
        if with_mcp:
            metrics.update(bench_mcp(repeat, calls=20 if scale == "full" else 5))
        return metrics


# ---------------------------------------------------------------------------
# Reporting and regression check
# ---------------------------------------------------------------------------


def render_table(metrics: dict[str, float]) -> str:
    lines = ["| Metric | Value | Unit |", "|---|---|---|"]
    for name, value in metrics.items():
        lines.append(f"| `{name}` | {value:.2f} | {UNITS[name]} |")
    return "\n".join(lines)


def check_against(
    current: dict[str, float], baseline: dict[str, float], fail_above: float
) -> list[str]:
    """Return human-readable regression violations (empty means healthy)."""
    problems: list[str] = []
    for name, now in current.items():
        direction = DIRECTIONS.get(name, "down")
        if direction == "info" or name not in baseline:
            continue
        base = baseline[name]
        if base <= 0:
            continue
        ratio = now / base if direction == "down" else base / now
        if ratio > fail_above:
            problems.append(
                f"{name}: {now:.2f} vs baseline {base:.2f} "
                f"({ratio:.1f}x {'slower' if direction == 'down' else 'lower'}, "
                f"limit {fail_above:.1f}x)"
            )
    return problems


def load_json(path: Path) -> dict[str, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): float(v) for k, v in data.items()}


def append_history(path: Path, metrics: dict[str, float], scale: str, repeat: int) -> None:
    """Append one JSON line to the hosted history file (benchmarks/history.jsonl).

    One line per run keeps the file append-only and diffable: each record
    carries the timestamp, the corpus scale, and the full metric dict, so a
    week-over-week comparison is a plain `git log -p` or a jq one-liner.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "scale": scale,
        "repeat": repeat,
        "seed": SEED,
        "metrics": metrics,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Master CI performance benchmark")
    parser.add_argument("--scale", choices=sorted(SCALES), default="full")
    parser.add_argument("--repeat", type=int, default=3, help="best-of-N timing runs")
    parser.add_argument("--json", type=Path, help="write results as JSON to this path")
    parser.add_argument("--save", type=Path, help="write a baseline file (same as --json)")
    parser.add_argument(
        "--append",
        type=Path,
        help="append one JSON line to this history file (benchmarks/history.jsonl)",
    )
    parser.add_argument(
        "--check",
        type=Path,
        help="compare against a saved baseline and fail on regressions",
    )
    parser.add_argument(
        "--fail-above",
        type=float,
        default=3.0,
        help="regression ratio that fails --check (default 3.0; CI runners are noisy)",
    )
    parser.add_argument("--no-mcp", action="store_true", help="skip the MCP stdio benchmarks")
    args = parser.parse_args(argv)

    metrics = run_suite(args.scale, args.repeat, with_mcp=not args.no_mcp)
    print(render_table(metrics))
    print(f"\ncorpus scale: {args.scale}, best of {args.repeat} runs, seed {SEED}")

    out = args.save or args.json
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"results written: {out}")

    if args.append:
        append_history(args.append, metrics, args.scale, args.repeat)
        print(f"history appended: {args.append}")

    if args.check:
        baseline = load_json(args.check)
        problems = check_against(metrics, baseline, args.fail_above)
        if problems:
            print("\nperformance regressions detected:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print(f"no regression above {args.fail_above}x baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
