"""Round-10 accuracy corpus: the four promotion-threshold columns.

The corpus protocol asks for five judged rows per check before its
promotion verdict can read anything but advisory-only. This tranche
grows csharp-async-void, csharp-broad-catch, rust-unwrap and
debug-print to that threshold with realistic rows, plus the clean
rows that pin each oracle's exemption logic (the On-prefix handler,
the exception-reading catch, the documented expect(), the stderr
writer). Contract tests pin the positive, the nearest negative and
the advisory flag, the same law test_bulk_aislop and its round-9
sibling establish: a future edit that flips advisory-ness fails here.

Module loads per the conftest convention: scripts/ is already on
sys.path, so the engines import directly and no preamble repeats.
"""

from detector_module import hits as _hits, scan

WORKER = """using System.Threading.Tasks;

namespace Pipeline.Workers
{
    public class QueueWorker
    {
        public async void ProcessQueue()
        {
            while (await _pump.MoveNextAsync())
            {
                await _pump.DispatchAsync();
            }
        }
    }
}
"""

ON_HANDLER = """using System.Threading.Tasks;

namespace Portal.Shell
{
    public class WindowEvents
    {
        public async void OnMessageReceived(Envelope envelope)
        {
            await _store.ApplyAsync(envelope);
        }
    }
}
"""


# ---------------------------------------------------------------------------
# csharp-async-void (reliability, advisory) - promotion-threshold rows
# ---------------------------------------------------------------------------


def test_async_void_queue_worker_flagged(tmp_path):
    findings = scan(tmp_path, {"Worker.cs": WORKER}, sub="av1")
    hits = _hits(findings, "csharp-async-void")
    assert len(hits) == 1
    assert hits[0].advisory
    assert "ProcessQueue" in hits[0].message


def test_async_void_on_prefix_exempt(tmp_path):
    findings = scan(tmp_path, {"WindowEvents.cs": ON_HANDLER}, sub="av2")
    assert not _hits(findings, "csharp-async-void")


def test_async_void_two_methods_two_findings(tmp_path):
    code = (
        WORKER.replace("ProcessQueue", "RefreshPanels")
        + "\n"
        + WORKER.replace("ProcessQueue", "StartBackgroundSync")
    )
    findings = scan(tmp_path, {"Dashboard.cs": code}, sub="av3")
    assert len(_hits(findings, "csharp-async-void")) == 2


def test_async_void_service_save_flagged(tmp_path):
    code = WORKER.replace("ProcessQueue", "SaveOrderAsync")
    findings = scan(tmp_path, {"OrderService.cs": code}, sub="av4")
    assert len(_hits(findings, "csharp-async-void")) == 1


# ---------------------------------------------------------------------------
# csharp-broad-catch (reliability, advisory) - promotion-threshold rows
# ---------------------------------------------------------------------------

CATCH = """namespace Ingest
{
    public class ConfigReader
    {
        public string Read()
        {
            try
            {
                return _file.Load();
            }
            catch (@CLAUSE@)
            {
                @BODY@
            }
        }
    }
}
"""


def test_broad_catch_log_and_default_flagged(tmp_path):
    code = CATCH.replace("@CLAUSE@", "Exception ex").replace(
        "@BODY@", '_logger.LogInformation("failed");\n                return null;'
    )
    findings = scan(tmp_path, {"Reader.cs": code}, sub="bc1")
    hits = _hits(findings, "csharp-broad-catch")
    assert len(hits) == 1
    assert hits[0].advisory


def test_broad_catch_system_qualified_flagged(tmp_path):
    code = CATCH.replace("@CLAUSE@", "System.Exception failure").replace(
        "@BODY@", "return Row.Empty();"
    )
    findings = scan(tmp_path, {"Parser.cs": code}, sub="bc2")
    assert len(_hits(findings, "csharp-broad-catch")) == 1


def test_broad_catch_ignoring_binding_flagged(tmp_path):
    code = CATCH.replace("@CLAUSE@", "Exception error").replace("@BODY@", "_queue.Reset();")
    findings = scan(tmp_path, {"Batch.cs": code}, sub="bc3")
    assert len(_hits(findings, "csharp-broad-catch")) == 1


def test_broad_catch_reading_binding_exempt(tmp_path):
    code = CATCH.replace("@CLAUSE@", "Exception ex").replace(
        "@BODY@",
        '_logger.LogError(ex, "append deferred");\n                _store.QueueRetry(change);',
    )
    findings = scan(tmp_path, {"Auditor.cs": code}, sub="bc4")
    assert not _hits(findings, "csharp-broad-catch")


def test_broad_catch_unnamed_catch_not_this_oracle(tmp_path):
    """The unnamed form carries no binding to ignore - the regex oracle skips it."""
    code = CATCH.replace("@CLAUSE@", "Exception").replace("@BODY@", "return Row.Empty();")
    findings = scan(tmp_path, {"Parser.cs": code}, sub="bc5")
    assert not _hits(findings, "csharp-broad-catch")


# ---------------------------------------------------------------------------
# rust-unwrap (reliability, advisory) - promotion-threshold rows
# ---------------------------------------------------------------------------

RUST = """pub struct Gauge {{
    value: f64,
}}

impl Gauge {{
    pub fn from_text(raw: &str) -> Gauge {{
        {line}
        Gauge {{ value: parsed }}
    }}
}}
"""


def test_rust_unwrap_parse_flagged(tmp_path):
    code = RUST.format(line="let parsed: f64 = raw.parse().unwrap();")
    findings = scan(tmp_path, {"ingest.rs": code}, sub="ru1")
    hits = _hits(findings, "rust-unwrap")
    assert len(hits) == 1
    assert hits[0].advisory


def test_rust_unwrap_map_lookup_flagged(tmp_path):
    code = 'pub fn region(&self) -> String {\n    self.values.get("region").unwrap().clone()\n}\n'
    findings = scan(tmp_path, {"store.rs": code}, sub="ru2")
    assert len(_hits(findings, "rust-unwrap")) == 1


def test_rust_unwrap_lock_flagged(tmp_path):
    code = "pub fn bump(&self) -> u32 {\n    let mut guard = self.state.lock().unwrap();\n    *guard += 1;\n    *guard\n}\n"
    findings = scan(tmp_path, {"bus.rs": code}, sub="ru3")
    assert len(_hits(findings, "rust-unwrap")) == 1


def test_rust_unwrap_before_cfg_test_only(tmp_path):
    """Unwraps before the marker are production; the test half stays silent."""
    code = (
        "pub fn staged() -> usize {\n"
        '    std::env::var("PIPELINE_STAGES").unwrap().len()\n'
        "}\n\n"
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    #[test]\n"
        "    fn reports_steps() {\n"
        "        let count = Some(3).unwrap();\n"
        "        assert_eq!(count, 3);\n"
        "    }\n"
        "}\n"
    )
    findings = scan(tmp_path, {"pipeline.rs": code}, sub="ru4")
    hits = _hits(findings, "rust-unwrap")
    assert len(hits) == 1
    assert "PIPELINE_STAGES" in hits[0].message or "unwrap" in hits[0].message


def test_rust_expect_documented_clean(tmp_path):
    code = RUST.format(line='let parsed: f64 = raw.parse().expect("gauge text is validated");')
    findings = scan(tmp_path, {"vault.rs": code}, sub="ru5")
    assert not _hits(findings, "rust-unwrap")


# ---------------------------------------------------------------------------
# debug-print (ai-integrity, advisory) - promotion-threshold rows
# ---------------------------------------------------------------------------

SERVICE = '''"""Invoice billing service for the storefront."""


class BillingService:
    def charge(self, invoice):
        print(f"charging order {invoice.order_id}")
        return self._gateway.charge(invoice.total_cents)
'''


def test_debug_print_service_module_flagged(tmp_path):
    findings = scan(tmp_path, {"billing_service.py": SERVICE}, sub="dp1")
    hits = _hits(findings, "debug-print")
    assert len(hits) == 1
    assert hits[0].advisory


def test_debug_print_multiple_sites_one_counted_finding(tmp_path):
    code = 'def sync_pair(source, target):\n    print("syncing", source)\n    print("copied")\n    print("done")\n'
    findings = scan(tmp_path, {"sync_jobs.py": code}, sub="dp2")
    hits = _hits(findings, "debug-print")
    assert len(hits) == 1
    assert "3x" in hits[0].message


def test_debug_print_model_factory_flagged(tmp_path):
    code = 'def make_pallet(sku, weight_kg):\n    pallet = Pallet(sku, weight_kg)\n    print("built pallet", pallet.sku)\n    return pallet\n'
    findings = scan(tmp_path, {"warehouse_model.py": code}, sub="dp3")
    assert len(_hits(findings, "debug-print")) == 1


def test_debug_print_stderr_writer_clean(tmp_path):
    code = (
        "import sys\n\n\nclass LogStream:\n"
        "    def warn(self, message):\n"
        '        self._stream.write(f"WARN {message}\\n")\n'
    )
    findings = scan(tmp_path, {"log_stream.py": code}, sub="dp4")
    assert not _hits(findings, "debug-print")


def test_debug_print_lone_word_in_string_clean(tmp_path):
    """The AST oracle cannot false-positive on a print mention inside a string."""
    code = 'def notice():\n    return "print the report at the desk"\n'
    findings = scan(tmp_path, {"clerk.py": code}, sub="dp5")
    assert not _hits(findings, "debug-print")
