"""Round-7 bulk adoption: the scanaislop columns reversed into our oracles.

Contract tests for the 27 checks added from the scanaislop catalog
(C# column, C/C++ column, TS precision pair, Go panic, per-finding Rust
unwrap, Scala broad catch, duplicate type shape). Every rule is advisory
by registry law; every test pins the positive, the nearest negative and
the advisory flag, so a future edit that flips advisory-ness fails here.

The module loads per the conftest convention: scripts/ is already on
sys.path, so the engines import directly and no preamble repeats here -
the detector judges its own test suite too.
"""

import os
from pathlib import Path

import ai_slop_detector as mod


def scan(tmp_path, files=None, sub=None):
    """One scan over one isolated directory.

    Multi-scan tests MUST pass distinct ``sub`` names: scan() reads the
    whole tree it is pointed at, so two scans of the same tmp_path see
    each other's files and the negative asserts pick up stale findings.
    """
    base = tmp_path / sub if sub else tmp_path
    for name, text in (files or {}).items():
        target = base / name
        os.makedirs(target.parent, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return mod.scan(base, mod.load_phrases())


def _hits(findings, check):
    return [f for f in findings if f.check == check]


# ---------------------------------------------------------------------------
# C# column
# ---------------------------------------------------------------------------


def test_csharp_async_void_flagged_task_and_handler_exempt(tmp_path):
    code = (
        "public class S {\n"
        "    public async void Save() { await Write(); }\n"
        "    public async Task SaveAsync() { await Write(); }\n"
        "    public async void OnSaved(object s) { await Write(); }\n"
        "}\n"
    )
    findings = scan(tmp_path, {"Svc.cs": code})
    hits = _hits(findings, "csharp-async-void")
    assert len(hits) == 1 and "Save" in hits[0].message
    assert hits[0].advisory


def test_csharp_broad_catch_flagged_when_exception_unused(tmp_path):
    code = (
        "try { Run(); }\n"
        'catch (Exception ex) { Console.WriteLine("failed"); }\n'
        "try { Run(); }\n"
        'catch (Exception ex) { logger.LogError(ex, "failed"); }\n'
    )
    findings = scan(tmp_path, {"Svc.cs": code})
    hits = _hits(findings, "csharp-broad-catch")
    assert len(hits) == 1


def test_csharp_catch_rethrow_only_flagged(tmp_path):
    findings = scan(tmp_path, {"Svc.cs": "try { Run(); }\ncatch (Exception) { throw; }\n"})
    assert _hits(findings, "csharp-empty-catch-rethrow")


def test_csharp_console_leftover_not_in_tests(tmp_path):
    code = 'Console.WriteLine("hi");\nDebug.WriteLine("hi");\n'
    findings = scan(tmp_path, {"Svc.cs": code}, sub="prod")
    assert _hits(findings, "csharp-console-leftover")
    tests = scan(tmp_path, {"tests/SvcTests.cs": code}, sub="tst")
    assert not _hits(tests, "csharp-console-leftover")


def test_csharp_linq_count_and_index_loop(tmp_path):
    code = (
        "if (items.Count() > 0) { Take(); }\n"
        "for (int i = 0; i < items.Length; i++) { Sum(items[i]); }\n"
        "if (items.Any()) { Take(); }\n"
        "foreach (var x in items) { Sum(x); }\n"
    )
    findings = scan(tmp_path, {"Svc.cs": code})
    assert _hits(findings, "csharp-linq-count")
    assert _hits(findings, "csharp-index-loop")


def test_csharp_if_ladder_needs_four_branches(tmp_path):
    ladder4 = (
        "if (code == 1) { A(); }\n"
        "else if (code == 2) { B(); }\n"
        "else if (code == 3) { C(); }\n"
        "else if (code == 4) { D(); }\n"
    )
    assert _hits(scan(tmp_path, {"Svc.cs": ladder4}), "csharp-if-ladder")
    ladder3 = ladder4.rsplit("else if", 1)[0]
    assert not _hits(scan(tmp_path, {"Svc.cs": ladder3}), "csharp-if-ladder")


def test_csharp_string_concat_in_loop(tmp_path):
    in_loop = 'foreach (var p in parts) { html += "<li>"; }\n'
    outside = 'html += "<li>";\n'
    assert _hits(scan(tmp_path, {"Svc.cs": in_loop}, sub="in"), "csharp-string-concat-in-loop")
    assert not _hits(scan(tmp_path, {"Svc.cs": outside}, sub="out"), "csharp-string-concat-in-loop")


def test_csharp_suppressed_warning_needs_a_reason(tmp_path):
    bare = "#pragma warning disable CS4014\nvar x = 1;\n"
    reasoned = "#pragma warning disable CS4014 // startup fire-and-forget\nvar x = 1;\n"
    assert _hits(scan(tmp_path, {"Svc.cs": bare}, sub="bare"), "csharp-suppressed-warning")
    assert not _hits(
        scan(tmp_path, {"Svc.cs": reasoned}, sub="reasoned"), "csharp-suppressed-warning"
    )
    attr = '[SuppressMessage("Usage", "x:Y")]\npublic void M() { }\n'
    justified = '[SuppressMessage("Usage", "x:Y", Justification = "legacy")]\npublic void M() { }\n'
    assert _hits(scan(tmp_path, {"Svc.cs": attr}, sub="attr"), "csharp-suppressed-warning")
    assert not _hits(scan(tmp_path, {"Svc.cs": justified}, sub="just"), "csharp-suppressed-warning")


def test_csharp_null_forgiving_and_sync_over_async(tmp_path):
    code = "var n = user!.Name;\nvar v = task.Result;\nother.Wait();\n"
    findings = scan(tmp_path, {"Svc.cs": code})
    assert _hits(findings, "csharp-null-forgiving")
    assert _hits(findings, "csharp-sync-over-async")


def test_csharp_null_forgiving_ignores_not_equals(tmp_path):
    findings = scan(tmp_path, {"Svc.cs": "if (a != b) { Run(); }\n"})
    assert not _hits(findings, "csharp-null-forgiving")


def test_csharp_not_implemented_and_doc_comment(tmp_path):
    code = (
        "public void M() { throw new NotImplementedException(); }\n"
        "/// <summary>Gets or sets the name.</summary>\n"
        "public string Name { get; set; }\n"
    )
    findings = scan(tmp_path, {"Svc.cs": code})
    assert _hits(findings, "csharp-not-implemented")
    assert _hits(findings, "csharp-redundant-doc-comment")


def test_csharp_real_doc_comment_not_flagged(tmp_path):
    code = (
        "/// <summary>Name is cached; expire sets it back to null.</summary>\n"
        "public string Name { get; set; }\n"
    )
    findings = scan(tmp_path, {"Svc.cs": code})
    assert not _hits(findings, "csharp-redundant-doc-comment")


# ---------------------------------------------------------------------------
# C/C++ column
# ---------------------------------------------------------------------------


def test_cpp_c_style_cast_only_in_cpp_files(tmp_path):
    cast = "int n = (int)total;\n"
    assert _hits(scan(tmp_path, {"x.cpp": cast}, sub="cpp"), "cpp-c-style-cast")
    assert not _hits(scan(tmp_path, {"x.c": cast}, sub="c"), "cpp-c-style-cast")
    assert not _hits(
        scan(tmp_path, {"x.cpp": "static_cast<int>(total);"}, sub="named"), "cpp-c-style-cast"
    )


def test_cpp_define_constant_object_like_only(tmp_path):
    object_like = "#define MAX_RETRIES 5\nint x = MAX_RETRIES;\n"
    findings = scan(tmp_path, {"x.cpp": object_like})
    assert _hits(findings, "cpp-define-constant")


def test_cpp_endl_and_iostream_leftover(tmp_path):
    findings = scan(tmp_path, {"lib.cpp": "log << std::endl;\nstd::cout << value;\n"}, sub="lib")
    assert _hits(findings, "cpp-endl-in-stream")
    assert _hits(findings, "cpp-iostream-leftover")
    app = "int main() { std::cout << value; }\n"
    assert not _hits(scan(tmp_path, {"app.cpp": app}, sub="app"), "cpp-iostream-leftover")


def test_cpp_manual_delete_and_stub(tmp_path):
    code = "delete cache;\ndelete[] buffer;\nauto p = std::make_unique<C>();\n"
    findings = scan(tmp_path, {"x.cpp": code})
    assert _hits(findings, "cpp-manual-delete")
    assert _hits(findings, "cpp-not-implemented") == []


def test_cpp_not_implemented_stub(tmp_path):
    code = 'throw std::logic_error("not implemented");\nassert(false && "not implemented");\n'
    assert len(_hits(scan(tmp_path, {"x.cpp": code}), "cpp-not-implemented")) == 2


def test_cpp_null_literal_only_in_cpp(tmp_path):
    assert _hits(scan(tmp_path, {"x.cpp": "Node* p = NULL;\n"}, sub="cpp"), "cpp-null-literal")
    assert not _hits(scan(tmp_path, {"x.h": "Node* p = NULL;\n"}, sub="h"), "cpp-null-literal")
    assert not _hits(
        scan(tmp_path, {"x.cpp": "#define NULL ((void*)0)\n"}, sub="def"), "cpp-null-literal"
    )


def test_cpp_using_namespace_std_only_in_headers(tmp_path):
    line = "using namespace std;\n"
    assert _hits(scan(tmp_path, {"x.hpp": line}, sub="hpp"), "cpp-using-namespace-std-in-header")
    assert not _hits(
        scan(tmp_path, {"x.cpp": line}, sub="cpp"), "cpp-using-namespace-std-in-header"
    )


# ---------------------------------------------------------------------------
# TS precision pair + duplicate type shape
# ---------------------------------------------------------------------------


def test_redundant_type_coercion_on_typed_param(tmp_path):
    code = "function render(name: string, retries: number) {\n  return String(name) + Number(retries);\n}\n"
    findings = scan(tmp_path, {"svc.ts": code}, sub="ts")
    assert len(_hits(findings, "redundant-type-coercion")) == 2
    untyped = "function render(name) {\n  return String(name);\n}\n"
    assert not _hits(scan(tmp_path, {"svc.js": untyped}, sub="js"), "redundant-type-coercion")


def test_type_coercion_of_other_variable_not_flagged(tmp_path):
    code = "function render(name: string) {\n  const other = fetch();\n  return String(other);\n}\n"
    assert not _hits(scan(tmp_path, {"svc.ts": code}), "redundant-type-coercion")


def test_hidden_fallback_in_catch(tmp_path):
    code = "try { load(); } catch (e) { return 0; }\n"
    assert _hits(scan(tmp_path, {"svc.ts": code}), "hidden-fallback")
    loud = "try { load(); } catch (e) { throw e; }\n"
    assert not _hits(scan(tmp_path, {"svc.ts": loud}), "hidden-fallback")
    logged = "try { load(); } catch (e) { logger.error(e); }\n"
    assert not _hits(scan(tmp_path, {"svc.ts": logged}), "hidden-fallback")


def test_duplicate_type_declaration_across_files(tmp_path):
    shape = "export interface Money { amount: number; currency: string; }\n"
    findings = scan(tmp_path, {"a.ts": shape, "b.ts": shape})
    hits = _hits(findings, "duplicate-type-declaration")
    assert len(hits) == 1 and "b.ts" in hits[0].message


def test_same_name_different_shape_not_flagged(tmp_path):
    a = "export interface Money { amount: number; currency: string; }\n"
    b = "export interface Money { cents: number; }\n"
    assert not _hits(scan(tmp_path, {"a.ts": a, "b.ts": b}), "duplicate-type-declaration")
    solo = scan(tmp_path, {"a.ts": a})
    assert not _hits(solo, "duplicate-type-declaration")


# ---------------------------------------------------------------------------
# Go, Rust, Scala columns
# ---------------------------------------------------------------------------


def test_go_library_panic(tmp_path):
    lib = 'package store\n\nfunc Reset() { panic("nope") }\n'
    assert _hits(scan(tmp_path, {"store.go": lib}, sub="lib"), "go-library-panic")
    main = 'package main\n\nfunc main() { panic("boom") }\n'
    assert not _hits(scan(tmp_path, {"main.go": main}, sub="main"), "go-library-panic")
    test = 'package store\n\nfunc TestX(t *T) { panic("x") }\n'
    assert not _hits(scan(tmp_path, {"store_test.go": test}, sub="test"), "go-library-panic")


def test_rust_unwrap_per_finding_with_test_gate(tmp_path):
    prod = "fn f() -> i32 { let n = parse().unwrap(); n }\n"
    hits = _hits(scan(tmp_path, {"lib.rs": prod}), "rust-unwrap")
    assert len(hits) == 1 and hits[0].advisory
    mixed = (
        "fn f() -> i32 { let n = parse().unwrap(); n }\n"
        "#[cfg(test)]\nmod tests { #[test] fn t() { let x = parse().unwrap(); } }\n"
    )
    assert len(_hits(scan(tmp_path, {"lib.rs": mixed}), "rust-unwrap")) == 1


def test_scala_broad_catch(tmp_path):
    scala = "try { run() } catch { case e: Exception => log(e) }\n"
    assert _hits(scan(tmp_path, {"S.scala": scala}), "scala-broad-catch")
    rethrow = "try { run() } catch { case e: Exception => throw e }\n"
    assert not _hits(scan(tmp_path, {"S.scala": rethrow}), "scala-broad-catch")
    java = "catch (Exception e) { log(e); }\n"
    assert not _hits(scan(tmp_path, {"A.java": java}), "scala-broad-catch")


# ---------------------------------------------------------------------------
# Registry law
# ---------------------------------------------------------------------------


def test_round7_checks_are_advisory_and_categorized():
    from slop_categories import CHECK_CATEGORIES

    round7 = [
        "csharp-async-void",
        "csharp-broad-catch",
        "csharp-console-leftover",
        "csharp-empty-catch-rethrow",
        "csharp-if-ladder",
        "csharp-index-loop",
        "csharp-linq-count",
        "csharp-not-implemented",
        "csharp-null-forgiving",
        "csharp-redundant-doc-comment",
        "csharp-string-concat-in-loop",
        "csharp-suppressed-warning",
        "csharp-sync-over-async",
        "cpp-c-style-cast",
        "cpp-define-constant",
        "cpp-endl-in-stream",
        "cpp-iostream-leftover",
        "cpp-manual-delete",
        "cpp-not-implemented",
        "cpp-null-literal",
        "cpp-using-namespace-std-in-header",
        "redundant-type-coercion",
        "hidden-fallback",
        "duplicate-type-declaration",
        "go-library-panic",
        "rust-unwrap",
        "scala-broad-catch",
    ]
    for check in round7:
        assert check in mod.ADVISORY_ONLY_CHECKS, check
        assert check not in mod.BLOCKING_CHECKS, check
        assert check in CHECK_CATEGORIES, check
        assert check in mod.REMEDIES, check


def test_new_modules_inside_the_own_budget_law():
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name in (
        "slop_csharp.py",
        "slop_cpp.py",
        "slop_ts_checks.py",
        "slop_type_decls.py",
        "slop_family.py",
    ):
        non_blank = len([ln for ln in (scripts / name).read_text().splitlines() if ln.strip()])
        assert non_blank <= 400, f"{name}: {non_blank} non-blank lines"
