import pytest

from app.evaluation.task_eval import local_suite
from app.evaluation.task_eval.cli import main


@pytest.mark.asyncio
async def test_suite_keeps_components_and_does_not_mask_unknown(monkeypatch):
    async def booking():
        return {"status": "PASS"}

    async def retrieval():
        return {
            "cases": [
                {
                    "retrieval": {"status": "PASS"},
                    "acl": {"status": "UNKNOWN"},
                    "answer": {"status": "UNKNOWN"},
                }
            ]
        }

    async def notification():
        return {"cases": [{"status": "PASS"}]}

    async def operations():
        return {"cases": [{"status": "PASS"}]}

    async def signed_identity():
        return {"status": "PASS"}

    monkeypatch.setattr(local_suite, "evaluate_booking", booking)
    monkeypatch.setattr(local_suite, "evaluate_signed_identity", signed_identity)
    monkeypatch.setattr(local_suite, "evaluate_retrieval", retrieval)
    monkeypatch.setattr(local_suite, "evaluate_notification_policy", notification)
    monkeypatch.setattr(local_suite, "evaluate_operations", operations)
    report = await local_suite.run_local_suite()
    assert report["suite_statuses"] == {
        "booking": "PASS",
        "signed_identity": "PASS",
        "retrieval": "INCOMPLETE",
        "notification": "PASS",
        "operations": "PASS",
    }
    assert report["status"] == "INCOMPLETE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("signed_status", "expected"),
    [("FAIL", "FAIL"), ("ERROR", "INCOMPLETE"), ("unrecognized", "INCOMPLETE")],
)
async def test_signed_identity_result_affects_summary(monkeypatch, signed_status, expected):
    async def result():
        return {"status": signed_status}

    monkeypatch.setattr(local_suite, "evaluate_signed_identity", result)
    report = await local_suite.run_local_suite("signed_identity")
    assert report["suite_statuses"]["signed_identity"] == expected
    assert report["status"] == expected
    assert "evaluation/task_eval/booking_signed_identity.py" in report["source_sha256"]


@pytest.mark.asyncio
async def test_signed_identity_error_does_not_disclose_details(monkeypatch):
    async def error():
        raise RuntimeError("sensitive test detail")

    monkeypatch.setattr(local_suite, "evaluate_signed_identity", error)
    report = await local_suite.run_local_suite("signed_identity")
    assert report["status"] == "INCOMPLETE"
    assert report["suites"]["signed_identity"]["status"] == "ERROR"
    assert "sensitive test detail" not in str(report)


@pytest.mark.asyncio
async def test_all_suites_keeps_signed_failure_over_other_unknown(monkeypatch):
    async def passed():
        return {"status": "PASS"}

    async def failed():
        return {"status": "FAIL", "checks": {"tampered_decision_denied": False}}

    async def retrieval():
        return {"cases": [{key: {"status": "UNKNOWN"} for key in ("retrieval", "acl", "answer")}]}

    async def component():
        return {"cases": [{"status": "PASS"}]}

    monkeypatch.setattr(local_suite, "evaluate_booking", passed)
    monkeypatch.setattr(local_suite, "evaluate_signed_identity", failed)
    monkeypatch.setattr(local_suite, "evaluate_retrieval", retrieval)
    monkeypatch.setattr(local_suite, "evaluate_notification_policy", component)
    monkeypatch.setattr(local_suite, "evaluate_operations", component)
    report = await local_suite.run_local_suite("all")
    assert report["suite_statuses"]["retrieval"] == "INCOMPLETE"
    assert report["suite_statuses"]["signed_identity"] == "FAIL"
    assert report["status"] == "FAIL"


def test_local_cli_returns_incomplete_and_writes_report(monkeypatch, tmp_path, capsys):
    async def fake(_):
        print("library log")
        return {"status": "INCOMPLETE"}

    monkeypatch.setattr(local_suite, "run_local_suite", fake)
    output = tmp_path / "report.json"
    assert main(["local", "--output", str(output)]) == 3
    captured = capsys.readouterr()
    assert "library log" in captured.err
    assert '"status": "INCOMPLETE"' in captured.out
    assert output.is_file()
    assert main(["local", "--output", str(output)]) == 1
