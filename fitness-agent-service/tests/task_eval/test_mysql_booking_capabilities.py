"""Metadata adapters never turn schema or mock observations into task success."""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from app.evaluation.task_eval import mysql_booking_capabilities as capability
from app.evaluation.task_eval.sandbox_preflight import PreflightError


def _result(value=0):
    return {
        "default_database": None,
        "identity": "root@localhost",
        "schema_present": 1,
        **{f"{table}_table": value for table in capability._TABLES},
        **{f"{table}_{column}": value for table, column in capability._COLUMNS},
    }


@pytest.mark.parametrize("value", [0, 1])
def test_schema_observation_never_establishes_business_capability(monkeypatch, value):
    calls = []
    monkeypatch.setattr(capability, "_container", lambda: ("a" * 16, "b" * 64))
    monkeypatch.setattr(
        capability, "_sql", lambda cid, sql: calls.append((cid, sql)) or json.dumps(_result(value))
    )
    result = capability.observe()
    assert result["case_status"] == {"BOOK-006": "UNKNOWN", "BOOK-009": "UNKNOWN"}
    assert result["business_capability_verified"] is False
    assert result["business_data_verified"] is False
    assert result["audit_complete"] is False
    assert all(item is bool(value) for item in result["observed_identifiers"].values())
    assert calls[0][0] == "b" * 64
    assert "information_schema" in calls[0][1]
    assert "DATABASE()" in calls[0][1] and "information_schema.schemata" in calls[0][1]
    assert all(
        word not in calls[0][1].upper()
        for word in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP ")
    )


@pytest.mark.parametrize(
    "change",
    [
        {"default_database": "production"},
        {"default_database": "fitness_eval"},
        {"schema_present": 0},
        {"schema_present": True},
        {"identity": "other@localhost"},
        {"appointment_store_id": True},
        {"appointment_store_id": 2},
        {"appointment_store_id": None},
        {"unknown_column": 0},
    ],
)
def test_invalid_schema_response_is_rejected(monkeypatch, change):
    monkeypatch.setattr(capability, "_container", lambda: ("a" * 16, "b" * 64))
    monkeypatch.setattr(capability, "_sql", lambda *_: json.dumps(_result() | change))
    with pytest.raises(PreflightError, match="BOOKING_SCHEMA_OBSERVATION_INVALID"):
        capability.observe()


def test_preflight_fails_before_any_schema_query(monkeypatch):
    def reject():
        raise PreflightError("CONTAINER_NOT_DISPOSABLE")

    monkeypatch.setattr(capability, "_container", reject)
    monkeypatch.setattr(capability, "_sql", lambda *_: pytest.fail("query must not execute"))
    with pytest.raises(PreflightError, match="CONTAINER_NOT_DISPOSABLE"):
        capability.observe()


@pytest.mark.parametrize(
    "raw", ['{"default_database":null,"default_database":"other"}', "{}", "not json"]
)
def test_duplicate_json_fields_and_incomplete_responses_are_rejected(monkeypatch, raw):
    monkeypatch.setattr(capability, "_container", lambda: ("a" * 16, "b" * 64))
    monkeypatch.setattr(capability, "_sql", lambda *_: raw)
    with pytest.raises(PreflightError, match="BOOKING_SCHEMA_OBSERVATION_INVALID"):
        capability.observe()


def test_cli_creates_private_report_and_does_not_replace_existing(tmp_path, monkeypatch, capsys):
    path = tmp_path / "report.json"
    monkeypatch.setattr(capability, "observe", lambda: {"case_status": {"BOOK-006": "UNKNOWN"}})
    assert capability.main(["--report-file", str(path)]) == 3
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["case_status"]["BOOK-006"] == "UNKNOWN"
    existing = path.read_bytes()
    assert capability.main(["--report-file", str(path)]) == 1
    assert path.read_bytes() == existing
    assert "REPORT_UNAVAILABLE" in capsys.readouterr().out


def test_cli_never_replaces_symlink_or_reveals_observer_errors(tmp_path, monkeypatch, capsys):
    target = tmp_path / "target"
    target.write_text("preserved")
    path = tmp_path / "link"
    path.symlink_to(target)
    assert capability.main(["--report-file", str(path)]) == 1
    assert target.read_text() == "preserved"
    fresh = tmp_path / "failed.json"

    def fail():
        raise RuntimeError("sensitive-database-error")

    monkeypatch.setattr(capability, "observe", fail)
    assert capability.main(["--report-file", str(fresh)]) == 1
    assert "sensitive-database-error" not in fresh.read_text()
    assert "sensitive-database-error" not in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["relative", "existing", "symlink", "extra"])
def test_launcher_rejects_invalid_capability_output_before_docker_or_maven(tmp_path, kind):
    marker = tmp_path / "called"
    for name in ("docker", "mvn"):
        executable = tmp_path / name
        executable.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
        executable.chmod(0o755)
    target = tmp_path / "target"
    target.write_text("preserved")
    path = tmp_path / "report.json"
    if kind == "existing":
        path.write_text("preserved")
    if kind == "symlink":
        path.symlink_to(target)
    output = "relative.json" if kind == "relative" else str(path)
    args = [
        "bash",
        str(Path(__file__).resolve().parents[2] / "scripts/task_eval_cross_service_booking.sh"),
        "--booking-capabilities-trial",
        output,
    ]
    if kind == "extra":
        args.append("unexpected")
    result = subprocess.run(
        args,
        capture_output=True,
        check=False,
        timeout=5,
        env={**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin"},
    )
    assert result.returncode != 0
    assert not marker.exists()
    assert target.read_text() == "preserved"
