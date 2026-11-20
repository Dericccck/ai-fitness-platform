import socket
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval import outreach_draft_fixtures as probes
from app.evaluation.task_eval.dataset import load_dataset
from app.notifications.preferences import (
    NotificationPolicyDecision,
    NotificationPreferenceRepository,
)

SOURCE = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


@pytest.mark.asyncio
async def test_eight_distinct_case_fixtures_need_no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("unexpected network access")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    report = await probes.evaluate(load_dataset(SOURCE))
    rows = report["cases"]
    assert [row["case_id"] for row in rows] == [f"OPER-{n:03}" for n in range(9, 17)]
    assert len({row["expected"]["recipient"] for row in rows}) == 8
    assert len({row["fixture"]["organization"] for row in rows}) == 8
    assert [row["component_grade"]["status"] for row in rows] == ["PASS"] * 5 + ["UNKNOWN"] * 3
    assert all(row["task_status"] == "UNKNOWN" for row in rows)
    assert (
        rows[3]["observation"]["observed"]["deliveries"][0]["recipient"]
        == rows[3]["expected"]["recipient"]
    )


def test_modified_case_and_fixture_fail_closed(monkeypatch, tmp_path):
    dataset = load_dataset(SOURCE)
    case = next(c for c in dataset.cases if c.case_id == "OPER-009")
    altered = case.model_copy(deep=True)
    altered.expected.reference_points = ["different"]
    changed = replace(
        dataset, cases=[altered if c.case_id == case.case_id else c for c in dataset.cases]
    )
    with pytest.raises(ValueError, match="OUTREACH_CASE_CHANGED"):
        probes.load_contracts(changed)
    fixture = tmp_path / "fixture.json"
    fixture.write_text("{}")
    monkeypatch.setattr(probes, "_FIXTURE_PATH", fixture)
    with pytest.raises(ValueError, match="OUTREACH_FIXTURE_CHANGED"):
        probes.load_contracts(dataset)


def test_delivery_evidence_must_be_scoped_and_complete():
    contract = probes.load_contracts(load_dataset(SOURCE))[3]
    row = {
        "component_status": "PASS",
        "observed": {
            "deliveries": [
                {"recipient": contract["subject"], "organization": contract["organization"]}
            ]
        },
    }
    assert probes._component_grade(contract, row)["status"] == "PASS"
    bad = deepcopy(row)
    bad["observed"]["deliveries"][0]["recipient"] = "synthetic-other"
    assert probes._component_grade(contract, bad)["status"] == "FAIL"
    bad = deepcopy(row)
    bad["observed"]["deliveries"][0]["organization"] = "synthetic-other"
    assert probes._component_grade(contract, bad)["status"] == "FAIL"
    assert (
        probes._component_grade(contract, {"component_status": "PASS", "observed": {}})["status"]
        == "UNKNOWN"
    )


@pytest.mark.asyncio
async def test_actual_worker_policy_bypass_causes_a_failing_component(monkeypatch):
    async def allow(self, connection, *, record, now=None):
        return NotificationPolicyDecision(action="ALLOW")

    monkeypatch.setattr(NotificationPreferenceRepository, "evaluate", allow)
    rows = (await probes.evaluate(load_dataset(SOURCE)))["cases"]
    assert {r["case_id"] for r in rows if r["component_grade"]["status"] == "FAIL"} >= {
        "OPER-009",
        "OPER-010",
        "OPER-011",
    }
    assert all(row["task_status"] == "UNKNOWN" for row in rows)


def test_command_reports_component_regression_in_exit_status(monkeypatch, capsys):
    async def failed(_dataset):
        return {"cases": [{"component_grade": {"status": "FAIL"}}]}

    monkeypatch.setattr(probes, "evaluate", failed)
    assert probes.main() == 1
    assert '"FAIL"' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_partial_probe_failure_is_not_hidden_by_incomplete_task(monkeypatch):
    async def partial_failure(_dataset, *, split, case_ids):
        return {"cases": [{"case_id": "OPER-014", "component_status": "FAIL"}]}

    monkeypatch.setattr(probes, "evaluate_outreach_case_observations", partial_failure)
    rows = (await probes.evaluate(load_dataset(SOURCE)))["cases"]
    assert rows[5]["component_grade"]["status"] == "FAIL"
    assert rows[5]["task_status"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_case_selection_does_not_execute_test_split_for_dev(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("test split must not run")

    monkeypatch.setattr(probes, "evaluate_outreach_case_observations", forbidden)
    dataset = load_dataset(SOURCE)
    rows = (await probes.evaluate(dataset, case_ids=frozenset({"OPER-009"})))["cases"]
    assert [row["case_id"] for row in rows] == ["OPER-009"]
    with pytest.raises(ValueError, match="OUTREACH_CASE_SELECTION_INVALID"):
        await probes.evaluate(dataset, case_ids=frozenset({"NOT-A-CASE"}))
