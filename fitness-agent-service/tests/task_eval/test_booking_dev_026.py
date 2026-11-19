import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.task_eval import booking_cases, booking_dev_026, booking_local
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).parents[2] / "evals/task_eval/datasets/v1.jsonl"


def case() -> Any:
    return next(item for item in load_dataset(DATASET).cases if item.case_id == "BOOK-026")


async def test_duplicate_approval_uses_resolved_message_and_tool_payload(monkeypatch: Any) -> None:
    fixture = booking_dev_026.load(case())
    actual_messages: list[str] = []
    actual_payloads: list[dict[str, Any]] = []
    original_chat = booking_local.AgentHTTP.chat
    original_model = booking_local.Model.chat_with_tools

    async def observe_chat(self: Any, message: str) -> Any:
        actual_messages.append(message)
        return await original_chat(self, message)

    async def observe_model(self: Any, *args: Any, **kwargs: Any) -> Any:
        actual_payloads.append(dict(self.payload))
        return await original_model(self, *args, **kwargs)

    monkeypatch.setattr(booking_local.AgentHTTP, "chat", observe_chat)
    monkeypatch.setattr(booking_local.Model, "chat_with_tools", observe_model)
    result = await booking_cases.run(case())
    assert actual_messages == [fixture["message"]]
    assert actual_payloads and all(payload == fixture["payload"] for payload in actual_payloads)
    assert result["status"] == "INCONCLUSIVE"
    assert result["evidence"]["fixture_sha256"] == fixture["sha256"]
    assert result["evidence"]["decision_requests"] == 2
    assert result["evidence"]["booking_count"] == 1
    assert result["evidence"]["credit_delta"] == -1
    assert next(a for a in result["assertions"] if a["id"] == "task_result")["status"] == "UNKNOWN"


def test_case_and_fixture_drift_fail_closed(monkeypatch: Any, tmp_path: Path) -> None:
    changed_case = case().model_copy(deep=True)
    changed_case.split = "test"
    with pytest.raises(ValueError, match="DEV_CASE_CHANGED"):
        booking_dev_026.load(changed_case)
    changed_case = case().model_copy(deep=True)
    changed_case.steps[2].fault = "business_timeout"
    with pytest.raises(ValueError, match="DEV_CASE_CHANGED"):
        booking_dev_026.load(changed_case)

    content = json.loads(booking_dev_026._FIXTURE.read_bytes())
    content["payload"]["course_id"] = "different-course"
    target = tmp_path / "fixture.json"
    target.write_text(json.dumps(content), encoding="utf-8")
    monkeypatch.setattr(booking_dev_026, "_FIXTURE", target)
    with pytest.raises(ValueError, match="DEV_FIXTURE_CHANGED"):
        booking_dev_026.load(case())
    monkeypatch.setattr(booking_dev_026, "_SHA256", hashlib.sha256(target.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="DEV_FIXTURE_INCONSISTENT"):
        booking_dev_026.load(case())


async def test_fixture_failure_cannot_return_a_successful_trial(
    monkeypatch: Any, tmp_path: Path
) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(booking_dev_026, "_FIXTURE", path)
    with pytest.raises(ValueError, match="DEV_FIXTURE_CHANGED"):
        await booking_cases.run(case())
