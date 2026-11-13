"""Contract tests for the flake-hunt parsers.

Every runner shape the hunt consumes - JUnit XML, TRX, go test JSON and
libtest text - must map verdicts to one uniform outcome model, and every
malformed input must raise ``FlakyParseError`` so the hunt can fail
closed (exit 2) instead of reporting a silent zero-test pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import flaky_parsers as fp  # noqa: E402


def outcomes(pairs: list[tuple[str, str, str]]) -> list[fp.Outcome]:
    return [fp.Outcome(*triple) for triple in pairs]


class TestJUnitXML:
    def test_verdicts_map_to_the_model(self):
        xml = """<testsuites><testsuite name="pkg">
        <testcase name="ok_test" classname="pkg"/>
        <testcase name="bad_test" classname="pkg"><failure>boom</failure></testcase>
        <testcase name="err_test" classname="pkg"><error>panic</error></testcase>
        <testcase name="skip_test" classname="pkg"><skipped/></testcase>
        </testsuite></testsuites>"""
        assert fp.parse_junit(xml) == outcomes(
            [
                ("pkg", "ok_test", "passed"),
                ("pkg", "bad_test", "failed"),
                ("pkg", "err_test", "failed"),
                ("pkg", "skip_test", "skipped"),
            ]
        )

    def test_classname_falls_back_to_the_suite_name(self):
        xml = '<testsuite name="root"><testcase name="t"/></testsuite>'
        assert fp.parse_junit(xml) == outcomes([("root", "t", "passed")])

    def test_bare_testsuite_root_is_accepted(self):
        xml = '<testsuite name="s"><testcase name="t"/></testsuite>'
        assert len(fp.parse_junit(xml)) == 1

    def test_key_joins_suite_and_name(self):
        assert fp.Outcome("pkg", "t", "passed").key == "pkg::t"
        assert fp.Outcome("", "t", "passed").key == "t"

    def test_nameless_testcase_fails_closed(self):
        with pytest.raises(fp.FlakyParseError, match="without a name"):
            fp.parse_junit('<testsuite name="s"><testcase classname="x"/></testsuite>')

    def test_malformed_xml_fails_closed(self):
        with pytest.raises(fp.FlakyParseError, match="malformed JUnit XML"):
            fp.parse_junit("<testsuite><testcase>")


class TestTrx:
    def test_verdicts_map_to_the_model(self):
        trx = """<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">
            <Results>
            <UnitTestResult testName="A" outcome="Passed"/>
            <UnitTestResult testName="B" outcome="Failed"/>
            <UnitTestResult testName="C" outcome="NotExecuted"/>
            </Results>
        </TestRun>"""
        assert fp.parse_trx(trx) == outcomes(
            [("", "A", "passed"), ("", "B", "failed"), ("", "C", "skipped")]
        )

    def test_unknown_outcome_fails_closed(self):
        trx = (
            '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
            '<Results><UnitTestResult testName="A" outcome="Inconclusive"/>'
            "</Results></TestRun>"
        )
        with pytest.raises(fp.FlakyParseError, match="unknown outcome"):
            fp.parse_trx(trx)

    def test_malformed_trx_fails_closed(self):
        with pytest.raises(fp.FlakyParseError, match="malformed TRX"):
            fp.parse_trx("<TestRun><Results>")


class TestGoJSON:
    def test_verdict_events_map_to_the_model(self):
        events = (
            '{"Time":"t","Action":"run","Package":"p","Test":"TestA"}\n'
            '{"Time":"t","Action":"pass","Package":"p","Test":"TestA"}\n'
            '{"Time":"t","Action":"fail","Package":"p","Test":"TestB"}\n'
            '{"Time":"t","Action":"skip","Package":"p","Test":"TestC"}\n'
        )
        assert fp.parse_go_json(events) == outcomes(
            [
                ("p", "TestA", "passed"),
                ("p", "TestB", "failed"),
                ("p", "TestC", "skipped"),
            ]
        )

    def test_stream_and_package_events_are_noise(self):
        events = (
            '{"Action":"start","Package":"p"}\n'
            '{"Action":"output","Package":"p","Test":"TestA","Output":"x"}\n'
            '{"Action":"pass","Package":"p"}\n'
        )
        assert fp.parse_go_json(events) == []

    def test_malformed_line_fails_closed(self):
        with pytest.raises(fp.FlakyParseError, match="line 1"):
            fp.parse_go_json("{not json}\n")

    def test_non_object_line_fails_closed(self):
        with pytest.raises(fp.FlakyParseError, match="not an object"):
            fp.parse_go_json("[1,2]\n")


class TestLibtest:
    def test_verdict_lines_map_to_the_model(self):
        text = "\n".join(
            [
                "running 3 tests",
                "test alpha ... ok",
                "test beta ... FAILED",
                "test gamma ... ignored",
                "",
                "test result: FAILED. 1 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out",
            ]
        )
        assert fp.parse_libtest(text) == outcomes(
            [("", "alpha", "passed"), ("", "beta", "failed"), ("", "gamma", "skipped")]
        )

    def test_duration_suffix_is_tolerated(self):
        assert fp.parse_libtest("test slow ... ok <2s>") == outcomes([("", "slow", "passed")])

    def test_empty_input_is_empty(self):
        assert fp.parse_libtest("") == []

    def test_non_libtest_text_fails_closed(self):
        with pytest.raises(fp.FlakyParseError, match="zero tests"):
            fp.parse_libtest("hello world\nnothing to see")


class TestDispatch:
    def test_unknown_suffix_fails_closed(self, tmp_path):
        path = tmp_path / "run-1.ini"
        path.write_text("[x]", encoding="utf-8")
        with pytest.raises(fp.FlakyParseError, match="unknown result format"):
            fp.parse_file(path)

    def test_every_known_suffix_dispatches(self, tmp_path):
        samples = {
            "run-1.xml": '<testsuite name="s"><testcase name="t"/></testsuite>',
            "run-2.gojson": '{"Action":"pass","Package":"p","Test":"T"}\n',
            "run-3.libtest": "test t ... ok\n",
            "run-4.trx": (
                '<TestRun xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010">'
                '<Results><UnitTestResult testName="t" outcome="Passed"/>'
                "</Results></TestRun>"
            ),
        }
        for name, text in samples.items():
            (tmp_path / name).write_text(text, encoding="utf-8")
            assert len(fp.parse_file(tmp_path / name)) == 1
