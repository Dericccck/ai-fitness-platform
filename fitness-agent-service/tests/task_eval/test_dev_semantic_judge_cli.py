import hashlib
import json
from pathlib import Path

import pytest

from app.evaluation.task_eval import dev_semantic_judge_cli as cli
from app.evaluation.task_eval.budget_store import DurableBudget
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"
ANSWER = "这是一个合成的待评审回答。"


def _private(path: Path, content: object) -> None:
    path.write_text(json.dumps(content, ensure_ascii=False))
    path.chmod(0o600)


def _args(tmp_path: Path) -> tuple[list[str], Path]:
    dataset = load_dataset(DATASET)
    observation = tmp_path / "observation.json"
    answer = tmp_path / "answer.json"
    key = tmp_path / "key"
    budget = tmp_path / "budget.sqlite"
    output = tmp_path / "proposal.json"
    _private(
        observation,
        {
            "case_id": "KNOW-020",
            "dataset_sha256": dataset.sha256,
            "component_status": "OBSERVED",
            "answer_sha256": hashlib.sha256(ANSWER.encode()).hexdigest(),
        },
    )
    _private(answer, {"case_id": "KNOW-020", "answer": ANSWER})
    key.write_text("synthetic-evaluation-key")
    key.chmod(0o600)
    DurableBudget(budget, limit_nano=500_000_000)
    return (
        [
            "--dataset",
            str(DATASET),
            "--observation",
            str(observation),
            "--answer-file",
            str(answer),
            "--credential-file",
            str(key),
            "--budget-file",
            str(budget),
            "--budget-limit-nano",
            "500000000",
            "--budget-opening-nano",
            "0",
            "--minimum-reserved-nano",
            "0",
            "--confirm-current-pricing",
            "--execute",
            "--output",
            str(output),
        ],
        output,
    )


def test_cli_emits_only_unreviewed_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_judge(*args, **kwargs):
        assert args[4] == ANSWER
        return {
            "component_status": "OBSERVED",
            "task_status": "UNKNOWN",
            "human_review": "NOT_REVIEWED",
        }

    monkeypatch.setattr(cli, "propose_judgment", fake_judge)
    args, output = _args(tmp_path)
    assert cli.main(args) == 3
    result = json.loads(output.read_text())
    assert result["task_status"] == "UNKNOWN" and result["human_review"] == "NOT_REVIEWED"
    assert ANSWER not in output.read_text()
    assert output.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("kind", ["public", "symlink", "duplicate_keys", "wrong_case"])
def test_invalid_private_input_does_not_call_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    async def forbidden(*args, **kwargs):
        raise AssertionError("must not call model")

    monkeypatch.setattr(cli, "propose_judgment", forbidden)
    args, output = _args(tmp_path)
    answer = tmp_path / "answer.json"
    if kind == "public":
        answer.chmod(0o644)
    elif kind == "symlink":
        link = tmp_path / "link"
        link.symlink_to(answer)
        args[args.index("--answer-file") + 1] = str(link)
    elif kind == "duplicate_keys":
        answer.write_text('{"case_id":"KNOW-020","case_id":"other","answer":"x"}')
    else:
        _private(answer, {"case_id": "other", "answer": ANSWER})
    assert cli.main(args) == 1
    assert json.loads(output.read_text())["status"] == "ERROR"


def test_existing_output_is_not_overwritten_or_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(*args, **kwargs):
        raise AssertionError("must not call model")

    monkeypatch.setattr(cli, "propose_judgment", forbidden)
    args, output = _args(tmp_path)
    output.write_text("existing")
    assert cli.main(args) == 1
    assert output.read_text() == "existing"
