"""Contract tests for the LCOM4 class-cohesion measurement (v2.12, P1).

Covers the documented contract in docs/checks.md:
- the split class reports, the cohesive class stays clean,
- constructors, dunders, statics and classmethods never glue groups,
- stateless orphans and stateless classes are dropped by policy,
- property reads join the graph like calls,
- JS/TS classes measure through the scanner view,
- registration, caps, ignore globs and malformed input behavior.
"""

import importlib.util
import sys
from pathlib import Path

MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ai_slop_detector.py"
spec = importlib.util.spec_from_file_location("ai_slop_detector", MOD_PATH)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def scan(tmp_path, files=None, extra_globs=None):
    for name, text in (files or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return mod.scan(tmp_path, mod.load_phrases(), extra_globs=extra_globs)


PY_SPLIT = """
class Account:
    def __init__(self):
        self.balance = 0
        self.transport = None

    def deposit(self, amount):
        self.balance += amount

    def withdraw(self, amount):
        self.balance -= amount

    def send(self, message):
        self.transport.deliver(message)

    def queue(self, message):
        self.transport.enqueue(message)
"""

PY_COHESIVE = """
class Stack:
    def __init__(self):
        self.items = []

    def push(self, item):
        self.items.append(item)

    def pop(self):
        return self.items.pop()

    def depth(self):
        return len(self.items)
"""

# Every excluded member touches BOTH attributes: if any of them entered
# the graph, it would glue the two responsibilities into one component
# and this class would look clean.
PY_EXCLUDED = """
class Service:
    def __init__(self):
        self.a = 1
        self.b = 2

    def one(self):
        return self.a

    def two(self):
        return self.b

    @staticmethod
    def util(x):
        return x + 1

    @classmethod
    def build(cls):
        return cls()

    def __repr__(self):
        return f"Service({self.a}, {self.b})"
"""

PY_STATELESS_ORPHAN = """
class Handler:
    def __init__(self):
        self.rows = []

    def handle(self, req):
        self.rows.append(req)

    def pure(self, x):
        return x * 2
"""

PY_STATELESS = """
class Math:
    def add(self, a, b):
        return a + b

    def sub(self, a, b):
        return a - b
"""

PY_ACCESSOR_PAIR = """
class Point:
    def __init__(self):
        self._x = 0

    def get_x(self):
        return self._x

    def set_x(self, value):
        self._x = value
"""

PY_PROPERTY = """
class Report:
    def __init__(self):
        self._rows = [1]

    @property
    def total(self):
        return sum(self._rows)

    def summary(self):
        return f"total={self.total}"
"""

PY_SIX_GROUPS = """
class Wideness:
    def one(self):
        return self.a1

    def two(self):
        return self.a2

    def three(self):
        return self.a3

    def four(self):
        return self.a4

    def five(self):
        return self.a5

    def six(self):
        return self.a6
"""

PY_MALFORMED = """
class Broken:
    def gone(self:
        self.x = 1
"""

JS_SPLIT = """
class Ledger {
  constructor() {
    this.balance = 0;
    this.wire = null;
  }
  deposit(amount) {
    this.balance += amount;
  }
  send(msg) {
    this.wire.deliver(msg);
  }
}
"""

JS_COHESIVE = """
class Cart {
  #items = [];
  add(item) {
    this.#items.push(item);
  }
  count() {
    return this.#items.length;
  }
}
"""

# The getter's property edge is the only bridge: summary() reads this.total
# (the property), the getter reads the private backing field shared with add().
JS_PROPERTY_EDGE = """
class Totalizer {
  #rows = [];
  add(row) {
    this.#rows.push(row);
  }
  get total() {
    return this.#rows.reduce((sum, row) => sum + row, 0);
  }
  summary() {
    return "total=" + this.total;
  }
}
"""

JS_STATIC_BRIDGE = """
class Api {
  constructor() {
    this.url = "";
    this.log = null;
  }
  fetch(path) {
    return fetch(this.url + path);
  }
  static make() {
    return new Api();
  }
  note(msg) {
    this.log.write(msg);
  }
}
"""

# Without the arrow-header shape this is two stateless singletons and one
# empty class view; with it, drain/fill split on left/right.
JS_ARROW_SPLIT = """
class Split {
  left = 0;
  right = 0;
  drain = (amount) => {
    this.left -= amount;
  };
  fill = (amount) => {
    this.right += amount;
  };
}
"""


def hits(findings):
    return [f for f in findings if f.check == "low-cohesion"]


# ---------------------------------------------------------------------------
# the measurement: split vs cohesive
# ---------------------------------------------------------------------------


def test_split_class_reports_low_cohesion(tmp_path):
    findings = scan(tmp_path, {"ledger.py": PY_SPLIT})
    found = hits(findings)
    assert len(found) == 1
    assert found[0].advisory and found[0].weight == 1
    assert found[0].path == "ledger.py"
    assert found[0].line == 2  # the class header line
    assert "LCOM4 2" in found[0].message
    assert "[deposit, withdraw]" in found[0].message
    assert "[send, queue]" in found[0].message


def test_cohesive_class_is_clean(tmp_path):
    findings = scan(tmp_path, {"stack.py": PY_COHESIVE})
    assert not hits(findings)


def test_constructor_dunder_static_and_classmethod_never_glue(tmp_path):
    findings = scan(tmp_path, {"service.py": PY_EXCLUDED})
    found = hits(findings)
    assert len(found) == 1
    assert "[one], [two]" in found[0].message


def test_stateless_orphan_component_is_dropped(tmp_path):
    findings = scan(tmp_path, {"handler.py": PY_STATELESS_ORPHAN})
    assert not hits(findings)


def test_stateless_class_has_nothing_to_measure(tmp_path):
    findings = scan(tmp_path, {"math.py": PY_STATELESS})
    assert not hits(findings)


def test_accessor_pair_sharing_one_attribute_is_cohesive(tmp_path):
    findings = scan(tmp_path, {"point.py": PY_ACCESSOR_PAIR})
    assert not hits(findings)


def test_property_read_joins_the_graph_like_a_call(tmp_path):
    findings = scan(tmp_path, {"report.py": PY_PROPERTY})
    assert not hits(findings)


def test_wide_class_renders_bounded_groups(tmp_path):
    findings = scan(tmp_path, {"wide.py": PY_SIX_GROUPS})
    found = hits(findings)
    assert len(found) == 1
    assert "LCOM4 6" in found[0].message
    assert "+2 more" in found[0].message  # 4 groups rendered, 2 elided


def test_self_call_connects_stateless_pair(tmp_path):
    # render touches state; render calls setup - one component, one group.
    text = """
class Page:
    def __init__(self):
        self.state = {}

    def setup(self):
        self.state["t"] = 1

    def render(self):
        self.setup()
        return self.state
"""
    assert not hits(scan(tmp_path, {"page.py": text}))


# ---------------------------------------------------------------------------
# JS/TS through the scanner view
# ---------------------------------------------------------------------------


def test_js_constructor_does_not_glue_split_class(tmp_path):
    findings = scan(tmp_path, {"ledger.js": JS_SPLIT})
    found = hits(findings)
    assert len(found) == 1
    assert found[0].path == "ledger.js"
    assert "class Ledger: LCOM4 2" in found[0].message


def test_js_private_fields_measure_cohesive(tmp_path):
    assert not hits(scan(tmp_path, {"cart.js": JS_COHESIVE}))


def test_js_getter_property_edge_is_the_only_bridge(tmp_path):
    assert not hits(scan(tmp_path, {"totalizer.js": JS_PROPERTY_EDGE}))


def test_js_static_method_is_excluded_from_the_graph(tmp_path):
    findings = scan(tmp_path, {"api.js": JS_STATIC_BRIDGE})
    found = hits(findings)
    assert len(found) == 1
    assert "[fetch], [note]" in found[0].message


def test_js_arrow_field_methods_split_and_measure(tmp_path):
    findings = scan(tmp_path, {"split.js": JS_ARROW_SPLIT})
    found = hits(findings)
    assert len(found) == 1
    assert "[drain], [fill]" in found[0].message


# ---------------------------------------------------------------------------
# scope, registry and robustness contracts
# ---------------------------------------------------------------------------


def test_test_files_are_exempt(tmp_path):
    findings = scan(tmp_path, {"tests/helper.py": PY_SPLIT, "src/app.py": PY_COHESIVE})
    assert not hits(findings)


def test_ignore_globs_are_respected(tmp_path):
    findings = scan(tmp_path, {"vendored/a.py": PY_SPLIT}, extra_globs=["vendored/"])
    assert not hits(findings)


def test_malformed_python_never_crashes_the_scan(tmp_path):
    findings = scan(tmp_path, {"broken.py": PY_MALFORMED})
    assert not hits(findings)


def test_files_without_classes_are_silent(tmp_path):
    findings = scan(tmp_path, {"plain.py": "x = 1\n", "plain.js": "const y = 2;\n"})
    assert not hits(findings)


def test_findings_capped_at_twelve(tmp_path):
    files = {f"pkg{i}/m.py": PY_SPLIT for i in range(14)}
    assert len(hits(scan(tmp_path, files))) == mod.MAX_LCOM4_FINDINGS


def test_low_cohesion_is_registered_advisory_maintainability():
    assert mod.category_of("low-cohesion") == "maintainability"
    assert "low-cohesion" in mod.REMEDIES
    assert mod.check_tier("low-cohesion") == 0.75  # maintainability pressure tier
    assert mod.Finding(path="x", line=1, check="low-cohesion", message="m", weight=1).advisory


# ---------------------------------------------------------------------------
# P1 acceptance: the benchmark fixture contract
# ---------------------------------------------------------------------------


def test_benchmark_fixture_two_unrelated_responsibilities(tmp_path):
    """The benchmark's "two unrelated class responsibilities" row."""
    fixture = """
class OrderManager:
    def __init__(self):
        self.rows = []
        self.smtp = None

    def charge(self, order):
        self.rows.append(order)

    def refund(self, order):
        self.rows.remove(order)

    def notify(self, customer):
        self.smtp.send(customer)

    def unsubscribe(self, customer):
        self.smtp.drop(customer)
"""
    found = hits(scan(tmp_path, {"orders.py": fixture}))
    assert len(found) == 1
    assert "[charge, refund]" in found[0].message
    assert "[notify, unsubscribe]" in found[0].message
