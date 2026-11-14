"""The churn-hotspot fixture corpus, built at run time with real git.

History cannot be checked in: a .git directory under fixtures/ would
leak git internals into the tree, and a fake history file would be
exactly the guessed count the hotspot contract forbids. So the runner
builds the corpus in a temp directory: init a repository, seed every
module with one commit, then commit stockroom.py thirty times under
realistic subjects - twelve fix-shaped, pricing the defect term three
times (churn 31, pressure 31 + 3 x 12 = 67, risk 67 x complexity 25 =
1675 over the 1200 budget). The scan root is the repo root itself, so
churn_map's --show-prefix filtering sees the same rel paths the density
corpus does.

Every generated file stays under thirty lines, ASCII-only, like the
checked-in fixtures; nineteen padding modules plus the dispatch file
clear the densities' 250-executable-line minimum, so the hotspot
measurement ranks a project, not a snippet. Any git failure returns
False and the caller reports PENDING - never a guessed count.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

GIT_COMMAND_TIMEOUT = 30  # seconds; a cold git call must never hang the build
BENCH_EMAIL = "bench@warehouse.invalid"
BENCH_NAME = "Fixture Bench"

# One decision branch per instruction kind: cyclomatic 25 for the file
# the history churns, well clear of the budget headroom the risk math needs.
HOTSPOT_KINDS = (
    "receive",
    "inspect",
    "restock",
    "relocate",
    "audit",
    "quarantine",
    "dispatch",
    "void",
    "recount",
    "merge",
    "split",
    "hold",
    "release",
    "return",
    "scrap",
    "adopt",
    "transfer",
    "cycle",
    "freeze",
    "thaw",
    "weigh",
    "stamp",
    "route",
    "stage",
)

# Thirty commits touching stockroom.py after the seed; twelve carry a
# fix-shaped subject (the hotspot engine's defect-term words).
HOTSPOT_COMMITS = (
    "tune stockroom receive thresholds",
    "fix stockroom rounding drift",
    "tighten stockroom inspect tolerances",
    "fix stockroom parity slip on rollover",
    "rebalance stockroom dispatch weights",
    "patch stockroom unit recount",
    "clarify stockroom dispatch fallback",
    "fix stockroom tolerance spill",
    "guard stockroom payload edges",
    "fix stockroom stage miscount",
    "extend stockroom recount ledger",
    "hotfix stockroom ledger stall",
    "narrow stockroom quarantine band",
    "fix stockroom cycle drift",
    "steady stockroom weigh cadence",
    "patch stockroom weigh drift",
    "align stockroom stage naming",
    "fix stockroom stamp omissions",
    "audit stockroom dispatch tails",
    "hotfix stockroom freeze stall",
    "smooth stockroom receive pacing",
    "fix stockroom route gaps",
    "document stockroom void rules",
    "patch stockroom thaw order",
    "bound stockroom recount windows",
    "stage stockroom merge drills",
    "ease stockroom hold thresholds",
    "fix stockroom transfer pairing",
    "round stockroom weigh batches",
    "batch stockroom release notes",
)

# (filename, docstring title) for the stable padding modules.
PAD_MODULES = (
    ("crate.py", "crate bay"),
    ("pallet.py", "pallet row"),
    ("carton.py", "carton nest"),
    ("drum.py", "drum rack"),
    ("sack.py", "sack loft"),
    ("tote.py", "tote shelf"),
    ("bundle.py", "bundle rail"),
    ("parcel.py", "parcel dock"),
    ("roll.py", "roll bay"),
    ("binrow.py", "bin gutter"),
    ("docket.py", "docket file"),
    ("forklift.py", "forklift lane"),
    ("gantry.py", "gantry span"),
    ("hatch.py", "hatch cover"),
    ("invoice.py", "invoice tray"),
    ("jitney.py", "jitney park"),
    ("kiln.py", "kiln room"),
    ("loft.py", "loft stairs"),
    ("mezzanine.py", "mezzanine walk"),
)

PAD_MODULE = '''"""{title} (bench fixture: stable padding module)."""

LIMIT = {limit}

def adjust(value):
    total = value + LIMIT
    if total > LIMIT:
        total = LIMIT
    return total

def scale(value, factor):
    return (value * factor) % LIMIT

def fold(values):
    accumulated = 0
    for item in values:
        accumulated += adjust(item)
    return accumulated
'''


def _stockroom_source(drift: int) -> str:
    """The dispatch file; `drift` changes per commit so history is real."""
    branches = "\n".join(
        f'    if kind == "{kind}": return stage_{kind}(payload)' for kind in HOTSPOT_KINDS
    )
    return (
        '"""Stockroom floor instructions (bench fixture: high-churn dispatch)."""\n'
        f"\nDRIFT = {drift}\n\n"
        "def route_instruction(kind, payload):\n"
        f"{branches}\n"
        "    return stage_unknown(payload)\n"
    )


def _pad_source(index: int) -> str:
    return PAD_MODULE.format(title=PAD_MODULES[index][1], limit=3 + index)


def _git(repo: Path, *args: str) -> bool:
    """One git command against the temp repo; False on any failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
            capture_output=True,
            text=True,
            timeout=GIT_COMMAND_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def build_git_fixture(repo: Path) -> bool:
    """Seed the temp warehouse repo, then commit stockroom.py thirty times.

    Real history, real git: init, local identity, one seed commit across
    the whole corpus, then one commit per subject touching stockroom.py
    (its DRIFT constant changes per commit, so every commit is real).
    False on any git failure - the caller reports PENDING with a reason,
    never a guessed count.
    """
    repo.mkdir(parents=True, exist_ok=True)
    corpus = {"stockroom.py": _stockroom_source(0)}
    corpus.update({name: _pad_source(index) for index, (name, _title) in enumerate(PAD_MODULES)})
    for name, text in corpus.items():
        (repo / name).write_text(text, encoding="utf-8")
    if not (
        _git(repo, "init", "--quiet")
        and _git(repo, "config", "user.email", BENCH_EMAIL)
        and _git(repo, "config", "user.name", BENCH_NAME)
    ):
        return False
    if not (
        _git(repo, "add", "-A") and _git(repo, "commit", "--quiet", "-m", "seed warehouse modules")
    ):
        return False
    for index, subject in enumerate(HOTSPOT_COMMITS, start=1):
        (repo / "stockroom.py").write_text(_stockroom_source(index), encoding="utf-8")
        if not (_git(repo, "add", "-A") and _git(repo, "commit", "--quiet", "-m", subject)):
            return False
    return True
