import json
from types import SimpleNamespace

import pytest

from app.evaluation.task_eval import mysql_private_slot_observer as observer
from app.evaluation.task_eval.sandbox_preflight import PreflightError

ARGS = {
    "run_id": "a" * 16,
    "container_id": "b" * 64,
    "organization_id": "org-1",
    "coach_id": "coach-1",
    "start_time": "2030-01-01T10:00:00+08:00",
    "end_time": "2030-01-01T11:00:00+08:00",
}


def test_reads_real_booking_conflict_predicate_without_claiming_settlement(monkeypatch):
    calls = []
    monkeypatch.setattr(observer, "_inspect", lambda value: {"id": value})
    monkeypatch.setattr(
        observer, "verify_container", lambda value, run_id: calls.append((value, run_id))
    )

    def execute(command, **kwargs):
        sql = command[-1]
        assert "(coach_id=" in sql and "OR temp_coach_id=" in sql
        assert "status IN (0,1,3,4,5)" in sql and "course_start_time <" in sql
        assert "course_end_time IS NULL OR course_end_time >" in sql
        assert "SET TRANSACTION READ ONLY" in sql
        return SimpleNamespace(
            stdout=json.dumps(
                {"database": "fitness_eval", "identity": "root@localhost", "rows": None}
            )
        )

    monkeypatch.setattr(observer.subprocess, "run", execute)
    result = observer.read_private_slot(**ARGS)
    assert calls == [({"id": ARGS["container_id"]}, ARGS["run_id"])]
    assert result["scope"]["startTime"] == "2030-01-01T02:00:00Z"
    assert result["rows"] == [] and result["snapshot_complete"] is True
    assert result["settlement_proven"] is False


@pytest.mark.parametrize("value", ["bad", "2030-01-01T10:00:00", "2030-01-01T10:00:00.1+08:00"])
def test_invalid_slot_time_is_rejected_before_container_access(monkeypatch, value):
    monkeypatch.setattr(
        observer, "_inspect", lambda _: pytest.fail("must reject before inspection")
    )
    with pytest.raises(PreflightError, match="INVALID_SLOT_TIME"):
        observer.read_private_slot(**{**ARGS, "start_time": value})


def test_scope_and_duplicate_rows_are_rejected(monkeypatch):
    row = {
        "id": "appointment-1",
        "organizationId": "org-1",
        "studentId": "student-1",
        "coachId": "coach-1",
        "temporaryCoachId": None,
        "courseId": "course-1",
        "startTime": "2030-01-01T02:00:00Z",
        "endTime": "2030-01-01T03:00:00Z",
    }
    monkeypatch.setattr(observer, "_inspect", lambda value: {"id": value})
    monkeypatch.setattr(observer, "verify_container", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        observer.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps(
                {"database": "fitness_eval", "identity": "root@localhost", "rows": [row, row]}
            )
        ),
    )
    with pytest.raises(PreflightError, match="PRIVATE_SLOT_ROWS_INVALID"):
        observer.read_private_slot(**ARGS)
