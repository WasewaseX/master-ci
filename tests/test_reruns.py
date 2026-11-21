"""Contract tests for the rerun inventory (C43, round-19).

The documented contracts, test-pinned:

- every job whose ``run_attempt`` exceeds one is a rerun row with its
    attempt count and the recovery verdict (recovered on success,
    still-failing on a failed conclusion, rerun-pending otherwise);
- a job without ``run_attempt`` is a recorded data gap - never a
    guessed attempt one;
- totals, deterministic row order, byte-identical output for identical
    payloads;
- exit codes: 0 on any healthy payload, 2 on malformed JSON or a
    payload that is not a jobs list.
"""

import json

import sem_reruns as reruns  # noqa: E402


def _payload(jobs: list[dict[str, object]]) -> str:
    return json.dumps({"jobs": jobs})


def test_rerun_jobs_are_inventoried_with_recovery_state():
    payload = _payload(
        [
            {"name": "master / python", "conclusion": "success", "run_attempt": 1},
            {"name": "master / ruff", "conclusion": "success", "run_attempt": 3},
            {"name": "master / mypy", "conclusion": "failure", "run_attempt": 2},
            {"name": "master / docs", "conclusion": "neutral", "run_attempt": 2},
        ]
    )
    inventory = reruns.build_inventory(payload)
    assert inventory["jobs"] == 4
    assert inventory["rerun_jobs"] == 3
    assert inventory["total_reruns"] == 1 + 2 + 1
    rows = {str(row["job"]): row for row in inventory["rows"]}
    assert rows["master / ruff"]["state"] == "recovered"
    assert rows["master / ruff"]["reruns"] == 2
    assert rows["master / mypy"]["state"] == "still-failing"
    assert rows["master / docs"]["state"] == "rerun-pending"


def test_attempt_one_jobs_never_count_as_reruns():
    payload = _payload([{"name": "job", "conclusion": "success", "run_attempt": 1}])
    inventory = reruns.build_inventory(payload)
    assert inventory["rerun_jobs"] == 0 and inventory["rows"] == []


def test_missing_run_attempt_is_a_data_gap_not_a_guess():
    payload = _payload([{"name": "legacy-job", "conclusion": "success"}])
    inventory = reruns.build_inventory(payload)
    assert inventory["gaps"] == [
        {
            "job": "legacy-job",
            "conclusion": "success",
            "gap": "run_attempt missing - the API did not report attempts",
        }
    ]
    assert inventory["rerun_jobs"] == 0


def test_rows_sort_by_attempts_then_name_and_are_deterministic():
    payload = _payload(
        [
            {"name": "beta", "conclusion": "success", "run_attempt": 2},
            {"name": "alpha", "conclusion": "success", "run_attempt": 3},
            {"name": "gamma", "conclusion": "success", "run_attempt": 3},
        ]
    )
    inventory = reruns.build_inventory(payload)
    order = [str(row["job"]) for row in inventory["rows"]]
    assert order == ["alpha", "gamma", "beta"]
    assert inventory == reruns.build_inventory(payload)


def test_cancelled_rerun_is_still_failing():
    payload = _payload([{"name": "flaky", "conclusion": "cancelled", "run_attempt": 2}])
    inventory = reruns.build_inventory(payload)
    assert inventory["rows"][0]["state"] == "still-failing"


def test_markdown_names_every_row_and_the_disclaimer():
    payload = _payload(
        [
            {"name": "beta", "conclusion": "success", "run_attempt": 2, "url": "https://run/2"},
            {"name": "ghost", "conclusion": "success"},
        ]
    )
    text = reruns.render_markdown(reruns.build_inventory(payload))
    assert "- beta: attempt 1 + 1 rerun(s), conclusion success - recovered (https://run/2)" in text
    assert "data gap - run_attempt missing" in text
    assert "does not gate" in text


def test_cli_exit_codes_and_report_file(tmp_path, capsys):
    payload_path = tmp_path / "jobs.json"
    payload_path.write_text(
        _payload([{"name": "a", "conclusion": "success", "run_attempt": 2}]),
        encoding="utf-8",
    )
    report_path = tmp_path / "inventory.json"
    assert reruns.main(["--jobs-json", str(payload_path), "--report", str(report_path)]) == 0
    report = json.loads(report_path.read_text())
    assert report["schema"] == "rerun-inventory-v1"
    assert report["rerun_jobs"] == 1
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert reruns.main(["--jobs-json", str(bad)]) == 2
    not_jobs = tmp_path / "not-jobs.json"
    not_jobs.write_text(json.dumps({"count": 3}), encoding="utf-8")
    assert reruns.main(["--jobs-json", str(not_jobs)]) == 2
    out = capsys.readouterr().out
    assert "Rerun inventory" in out


def test_a_dict_that_is_neither_jobs_nor_a_list_is_a_configuration_error():
    assert reruns.build_inventory(_payload([]))["jobs"] == 0
    assert reruns.build_inventory(json.dumps([{"name": "a", "run_attempt": 1}]))["jobs"] == 1
    for bad in (json.dumps({"count": 3}), json.dumps({"jobs": {"count": 3}}), json.dumps([1, 2])):
        try:
            reruns.build_inventory(bad)
        except ValueError:
            continue
        raise AssertionError(f"payload must refuse: {bad}")
