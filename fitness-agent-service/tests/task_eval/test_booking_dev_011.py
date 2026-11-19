from pathlib import Path
from typing import Any

import pytest

from app.evaluation.task_eval import booking_cases, booking_dev_011
from app.evaluation.task_eval.dataset import load_dataset
from app.infrastructure.model_gateway import ModelTurn

DATASET = Path(__file__).parents[2] / "evals/task_eval/datasets/v1.jsonl"


def case() -> Any:
    return next(c for c in load_dataset(DATASET).cases if c.case_id == "BOOK-011")


async def test_book_011_probes_are_not_complete_task() -> None:
    result = await booking_cases.run(case())
    assert result["status"] == "INCONCLUSIVE"
    assert result["executed"] is True
    assert all(result["evidence"]["checks"].values())
    assert result["assertions"][0]["status"] == "UNKNOWN"
    assert "STORE_SELECTION_CONTRACT_UNAVAILABLE" in result["limitations"]
    assert "SCRIPTED_MODEL" in result["limitations"]


async def test_scripted_response_mismatch_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def wrong(self: Any, messages: Any, **kwargs: Any) -> ModelTurn:
        self.calls += 1
        return ModelTurn(content="已预约", tool_calls=())

    monkeypatch.setattr(booking_dev_011._ClarifyingModel, "chat_with_tools", wrong)
    result = await booking_cases.run(case())
    assert result["status"] == "FAIL"
    assert not result["evidence"]["checks"]["scripted_clarification_delivered"]


def test_case_change_fails_closed() -> None:
    changed = case().model_copy(deep=True)
    changed.steps[0].message = "帮我约一节私教课，不用询问"
    assert not booking_cases.supports(changed)
