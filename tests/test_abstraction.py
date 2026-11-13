"""Contract tests for the abstraction-overhead slice (P1 metric layer).

Covers the documented contract:
- a ladder of FORWARD_CHAIN_DEPTH same-target rungs reports one
    forwarding-chain finding naming every hop and the hop count,
- ladders under the depth budget, a rung that transforms and differing
    base attributes all stay silent,
- module functions chaining by bare target name report like methods and
    an async forward joins its ladder,
- a class whose every method forwards to one injected object reports as
    no-behavior-class; a real method among the forwards, the depth budget,
    protocol, dataclass, exception and enum shapes all stay silent,
- __init__ that only wires the forwarded attribute does not save a shell,
- robustness: non-python families, syntax errors, empty text,
- determinism and the self-scan contract on the detector's own source.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detector_module import detector as mod  # noqa: E402
from slop_abstraction import (  # noqa: E402
    FORWARD_CHAIN_DEPTH,
    NO_BEHAVIOR_MIN_METHODS,
    check_abstraction,
)


def run(text: str, family: str = "py"):
    return check_abstraction(Path("mod.py"), text, family)


def ladder(rungs: int, attr: str = "board", overrides=None, async_at=None) -> str:
    """A same-name publish ladder; every rung forwards to attr.publish."""
    overrides = overrides or {}
    blocks = []
    for index in range(rungs):
        expr = overrides.get(index, f"self.{attr}.publish(issue)")
        head = "    def publish(self, issue):"
        keyword = "return"
        if index == async_at:
            head = "    async def publish(self, issue):"
            keyword = "return await"
        blocks.append(f"class ReportLayer{index}:\n{head}\n        {keyword} {expr}\n")
    return "\n" + "\n\n".join(blocks)


def shell(methods: int, opening: str = "class ReportFacade:") -> str:
    """A facade whose every method forwards down one gateway attribute."""
    bodies = []
    for index in range(methods):
        bodies.append(
            f"    def channel{index}(self, report):\n"
            f"        return self.gateway.deliver{index}(report)\n"
        )
    return "\n" + opening + "\n" + "\n".join(bodies)


def wrappers(names, target: str = "dispatch") -> str:
    """Module functions that all forward to one bare target name."""
    return "\n" + "\n\n".join(f"def {name}(issue):\n    return {target}(issue)\n" for name in names)


WRAPPER_NAMES = ("rest_publish", "cli_publish", "job_publish", "api_publish")

MIXED_CLASS = """
class ReportFacade:
    def send(self, report):
        return self.gateway.deliver(report)

    def track(self, report):
        return self.gateway.observe(report)

    def audit(self, report):
        stamped = self.clock.stamp(report)
        return self.gateway.observe(stamped)
"""

INIT_SHELL = """
class ReportFacade:
    def __init__(self, gateway):
        self.gateway = gateway

    def send(self, report):
        return self.gateway.deliver(report)

    def track(self, report):
        return self.gateway.observe(report)
"""

INIT_BUSY = """
class ReportFacade:
    def __init__(self, gateway, clock):
        self.gateway = gateway
        self.clock = clock

    def send(self, report):
        return self.gateway.deliver(report)

    def track(self, report):
        return self.gateway.observe(report)
"""


def test_depth_ladder_flags_with_every_hop_and_the_count():
    hits = run(ladder(FORWARD_CHAIN_DEPTH))
    assert len(hits) == 1
    finding = hits[0]
    assert finding.check == "forwarding-chain"
    assert finding.advisory and finding.weight == 2
    assert finding.path == "mod.py"
    for index in range(FORWARD_CHAIN_DEPTH):
        assert f"ReportLayer{index}.publish" in finding.message
    assert f"({FORWARD_CHAIN_DEPTH} hops to the same call, none transforms it)" in finding.message
    assert finding.line == 3  # the first hop's def line


def test_ladder_under_the_depth_budget_stays_silent():
    assert not run(ladder(FORWARD_CHAIN_DEPTH - 1))


def test_transforming_rung_breaks_the_chain():
    overrides = {3: 'self.board.publish(issue, priority="high")'}
    assert not run(ladder(FORWARD_CHAIN_DEPTH, overrides=overrides))


def test_differing_base_attribute_breaks_the_chain():
    assert not run(ladder(FORWARD_CHAIN_DEPTH, overrides={1: "self.desk.publish(issue)"}))


def test_module_function_wrappers_chain_by_target_name():
    hits = run(wrappers(WRAPPER_NAMES))
    assert len(hits) == 1
    assert "4 hops" in hits[0].message
    for name in WRAPPER_NAMES:
        assert name in hits[0].message


def test_async_forward_joins_the_ladder():
    hits = run(ladder(FORWARD_CHAIN_DEPTH, async_at=2))
    assert len(hits) == 1
    assert "4 hops" in hits[0].message


def test_no_behavior_class_flags_when_every_method_forwards():
    hits = run(shell(NO_BEHAVIOR_MIN_METHODS))
    assert len(hits) == 1
    finding = hits[0]
    assert finding.check == "no-behavior-class"
    assert finding.advisory and finding.weight == 2
    assert "ReportFacade" in finding.message


def test_init_wiring_does_not_save_a_shell():
    assert len(run(INIT_SHELL)) == 1
    assert not run(INIT_BUSY)  # a second attribute means real setup behavior


def test_real_method_among_forwards_keeps_the_class_silent():
    assert not run(MIXED_CLASS)
    assert not run(shell(NO_BEHAVIOR_MIN_METHODS - 1))


def test_declaration_shaped_classes_stay_silent():
    assert not run(shell(2, "class ReportSink(Protocol):"))
    assert not run(shell(2, "@dataclass\nclass ReportJob:"))
    assert not run(shell(2, "class ReportError(Exception):"))
    assert not run(shell(2, "class ReportChannel(Enum):"))


def test_non_python_family_stays_silent():
    assert run(ladder(FORWARD_CHAIN_DEPTH), family="js") == []


def test_syntax_error_degrades_to_silent():
    assert run("def gone(:\n    return\n") == []


def test_empty_text_stays_silent():
    assert run("") == []
    assert run("   \n  \n") == []


def test_same_input_yields_identical_findings():
    text = ladder(FORWARD_CHAIN_DEPTH) + INIT_SHELL
    findings = run(text)
    assert len(findings) == 2
    assert run(text) == findings
    assert [finding.check for finding in findings] == [
        "forwarding-chain",
        "no-behavior-class",
    ]


def test_detector_source_stays_clean_under_the_engine():
    """Self-scan contract: the engine must not flag the detector itself."""
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert check_abstraction(Path(mod.__file__).name, source, "py") == []


def test_abstraction_checks_are_registered_advisory_maintainability():
    for check in ("forwarding-chain", "no-behavior-class"):
        assert mod.category_of(check) == "maintainability"
        assert check in mod.REMEDIES
        assert mod.check_tier(check) == 0.75  # maintainability pressure tier
        registered = mod.Finding(path="x", line=1, check=check, message="m", weight=2)
        assert registered.advisory
