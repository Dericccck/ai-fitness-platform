import json

import pytest

from app.evaluation.task_eval.book_009_local import _check


def output(**changes):
    preflight = {"dependency_status": "VERIFIED_DISPOSABLE_LOCAL_MYSQL"}
    result = {
        "status": "PASS",
        "scope": "java_private_coach_slot_concurrency",
        "origin": "java_booking_service_real_mysql",
        "source_sha256": "a" * 64,
        "source_attestation": "NOT_INDEPENDENTLY_VERIFIED",
        "model": "not_exercised",
        "gateway_http": "not_exercised",
        "audit_complete": False,
        "task_status": "UNKNOWN",
    }
    result.update(changes)
    return (json.dumps(preflight) + "\n" + json.dumps(result) + "\n").encode()


def test_real_mysql_component_is_not_a_task_pass():
    result = _check(output())
    assert result["status"] == "PASS"
    assert result["task_status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "change",
    [
        {"task_status": "PASS"},
        {"audit_complete": True},
        {"model": "exercised"},
        {"source_sha256": "invalid"},
        {"scope": "other"},
        {"status": "FAIL"},
    ],
)
def test_invalid_or_overstated_observation_is_rejected(change):
    with pytest.raises(ValueError):
        _check(output(**change))


def test_invalid_output_is_rejected():
    with pytest.raises((ValueError, UnicodeError)):
        _check(b"x" * 65537)
