from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval.contracts import Step
from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.split_leakage import audit_split_overlap

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


def _case(base, case_id, split, messages):
    steps = [Step(type="chat", message=message) for message in messages]
    return base.model_copy(update={"case_id": case_id, "split": split, "steps": steps})


def test_candidates_preserve_split_and_do_not_expose_text():
    dataset = load_dataset(DATASET)
    base = dataset.cases[0]
    cases = [
        _case(base, "DEV", "dev", ["预约今天下午的私教课程！"]),
        _case(base, "EXACT", "test", ["预约 今天下午的私教课程"]),
        _case(base, "NEAR", "test", ["预约今天下午的私教训练课程"]),
        _case(base, "OTHER", "test", ["我的合同能退款吗"]),
    ]
    result = audit_split_overlap(replace(dataset, cases=cases), threshold=0.65)
    assert [(pair["test_case_id"], pair["kind"]) for pair in result["pairs"]] == [
        ("EXACT", "normalized_exact"),
        ("NEAR", "character_trigram_dice"),
    ]
    assert "预约" not in str(result)
    assert result["coverage"]["test"] == {"total": 3, "compared": 3, "not_compared": 0}


def test_unresolved_and_no_chat_are_explicitly_not_compared():
    dataset = load_dataset(DATASET)
    base = dataset.cases[0]
    ref_case = base.model_copy(update={"steps": [Step(type="chat", message_ref="private")]})
    no_chat = base.model_copy(update={"steps": [Step(type="assert", phase="final")]})
    result = audit_split_overlap(replace(dataset, cases=[ref_case, no_chat]))
    assert result["pairs"] == []
    assert result["coverage"]["dev"]["not_compared"] == 2


@pytest.mark.parametrize("threshold", [0, -1, 1.1, float("nan"), True])
def test_invalid_threshold_rejected(threshold):
    dataset = load_dataset(DATASET)
    with pytest.raises(ValueError):
        audit_split_overlap(dataset, threshold=threshold)
