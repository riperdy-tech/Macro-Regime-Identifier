"""Section 3 of the 2026-11-12 MRI audit: one small status file a monitor can read.

The events job failed seven days running and nothing said so; the daily record said success with 0
warnings while the macro step had 41 to 46.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "job_status", Path(__file__).resolve().parent.parent / "scripts" / "job_status.py"
)
job_status = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(job_status)


def _doc(outputs: Path) -> dict:
    def refuse(token):
        raise ValueError(token)

    return json.loads((outputs / "job_status.json").read_text(encoding="utf-8"), parse_constant=refuse)


def test_start_then_success_records_start_finish_success_and_warnings(tmp_path: Path):
    job_status.record_job_status(tmp_path, "macro_daily", "start", now="2026-11-12T21:30:00+00:00")
    job_status.record_job_status(
        tmp_path, "macro_daily", "finish", exit_code=0, warnings=44,
        non_fatal_failures=["sector price panel"], now="2026-11-12T21:41:00+00:00",
    )
    record = _doc(tmp_path)["jobs"]["macro_daily"]

    assert record == {
        "job": "macro_daily",
        "last_start": "2026-11-12T21:30:00+00:00",
        "last_finish": "2026-11-12T21:41:00+00:00",
        "last_success": "2026-11-12T21:41:00+00:00",
        "last_exit_code": 0,
        "warnings": 44,
        "non_fatal_failures": ["sector price panel"],
    }


def test_a_failed_run_keeps_the_previous_success_and_records_its_exit_code(tmp_path: Path):
    job_status.record_job_status(tmp_path, "events_daily", "finish", exit_code=0, warnings=0, now="2026-10-22T04:03:00+00:00")
    job_status.record_job_status(tmp_path, "events_daily", "start", now="2026-10-23T04:00:00+00:00")
    job_status.record_job_status(tmp_path, "events_daily", "finish", exit_code=1, now="2026-10-23T04:00:09+00:00")
    record = _doc(tmp_path)["jobs"]["events_daily"]

    assert record["last_exit_code"] == 1
    assert record["last_success"] == "2026-10-22T04:03:00+00:00"
    assert record["last_start"] == "2026-10-23T04:00:00+00:00"
    assert record["warnings"] is None  # the job did not get far enough to say


def test_two_jobs_share_the_file_without_clobbering_each_other(tmp_path: Path):
    job_status.record_job_status(tmp_path, "macro_daily", "start", now="2026-11-12T21:30:00+00:00")
    job_status.record_job_status(tmp_path, "events_daily", "start", now="2026-11-12T04:00:00+00:00")
    jobs = _doc(tmp_path)["jobs"]

    assert set(jobs) == {"macro_daily", "events_daily"}
    assert jobs["macro_daily"]["last_start"] == "2026-11-12T21:30:00+00:00"
    assert not list(tmp_path.glob("*.tmp"))


def test_a_corrupt_status_file_is_replaced_not_fatal(tmp_path: Path):
    (tmp_path / "job_status.json").write_text("{not json", encoding="utf-8")
    job_status.record_job_status(tmp_path, "macro_daily", "start", now="2026-11-12T21:30:00+00:00")
    assert _doc(tmp_path)["jobs"]["macro_daily"]["last_start"] == "2026-11-12T21:30:00+00:00"


def test_the_command_line_reads_the_warning_count_from_the_job_record(tmp_path: Path):
    record = tmp_path / "daily_diagnostic_summary.json"
    record.write_text(json.dumps({"warnings": ["a", "b", "c"]}), encoding="utf-8")

    assert job_status.main(["--job", "macro_daily", "--outputs-dir", str(tmp_path), "start"]) == 0
    assert job_status.main(
        ["--job", "macro_daily", "--outputs-dir", str(tmp_path), "finish", "--exit-code", "0",
         "--warnings-from", str(record), "--non-fatal", "step one", "--non-fatal", "step two"]
    ) == 0
    stored = _doc(tmp_path)["jobs"]["macro_daily"]

    assert stored["warnings"] == 3
    assert stored["non_fatal_failures"] == ["step one", "step two"]
    assert stored["last_exit_code"] == 0


def test_a_status_write_failure_never_fails_the_job(tmp_path: Path, capsys):
    blocked = tmp_path / "outputs"
    blocked.write_text("a file where the folder should be", encoding="utf-8")

    assert job_status.main(["--job", "macro_daily", "--outputs-dir", str(blocked), "start"]) == 0
    assert "not written" in capsys.readouterr().err
