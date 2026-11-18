import copy
import json
import os
import stat
from pathlib import Path

import httpx
import pytest

from app.evaluation.task_eval.budget_store import DurableBudget
from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.dev_review_probe import (
    REVIEW_OUTPUT_LIMIT,
    _Gateway,
    _ProbeFailure,
    _sanitized,
    main,
    observe_dev_case,
)

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


def response(content="测试回答"):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 150,
            "completion_tokens": 25,
            "total_tokens": 175,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 150,
        },
    }


def private(tmp_path):
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    return path


def test_review_material_redacts_common_credentials_and_personal_identifiers():
    text = (
        "api_key=other-token password: abc123 bearer bearer-token someone@example.com "
        "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----"
    )
    cleaned = _sanitized(text, "the-secret-key")
    for secret in (
        "other-token",
        "abc123",
        "bearer-token",
        "someone@example.com",
        "private-material",
    ):
        assert secret not in cleaned


@pytest.mark.asyncio
async def test_model_output_is_review_only_redacted_and_never_auto_passed(tmp_path):
    path = private(tmp_path)
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000)
    reqs = []

    def handler(req):
        reqs.append(req)
        body = json.loads(req.content)
        assert body["messages"][-1]["content"] == "深蹲上推时怎么呼吸？"
        assert body["max_tokens"] == 1024 == REVIEW_OUTPUT_LIMIT
        assert "tools" not in body
        return httpx.Response(
            200,
            json=response("继续高强度训练，sk-AbCdEf01234567890 secret-private-key 13812345678"),
        )

    result = await observe_dev_case(
        load_dataset(DATASET),
        "KNOW-001",
        credential="secret-private-key",
        budget=budget,
        review_directory=path,
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(handler),
    )
    assert result["attempts"] == 1 and len(reqs) == 1
    assert result["task_status"] == "UNKNOWN" and result["human_review"] == "NOT_REVIEWED"
    assert result["component_status"] == "OBSERVED"
    dumped = json.dumps(result, ensure_ascii=False)
    assert "secret-private-key" not in dumped and "继续高强度训练" not in dumped
    review_file = path / result["review_file"]
    assert stat.S_IMODE(review_file.stat().st_mode) == 0o600
    text = review_file.read_text()
    assert (
        "secret-private-key" not in text
        and "sk-AbCdEf01234567890" not in text
        and "13812345678" not in text
    )
    assert "继续高强度训练" in text
    assert json.loads(text)["review_status"] == "NOT_REVIEWED"
    assert budget.audit()[-1]["details"] == {"state": "COMPLETE", "reason_code": "UNSPECIFIED"}
    assert result["reserved_cost_nano"] == 163_840_000


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["timeout", "tool", "usage", "oversize"])
async def test_bad_response_freezes_and_no_retry(tmp_path, kind):
    path = private(tmp_path)
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000)
    reqs = []

    def handler(req):
        reqs.append(req)
        if kind == "timeout":
            raise httpx.ReadTimeout("secret", request=req)
        if kind == "oversize":
            return httpx.Response(200, content=b"x" * 65537)
        payload = response()
        if kind == "tool":
            payload["choices"][0]["message"]["tool_calls"] = [{"function": {"name": "write"}}]
        if kind == "usage":
            payload["usage"]["completion_tokens"] = 10000
        return httpx.Response(200, json=payload)

    result = await observe_dev_case(
        load_dataset(DATASET),
        "KNOW-001",
        credential="secret-private-key",
        budget=budget,
        review_directory=path,
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(handler),
    )
    assert result["component_status"] == "INCOMPLETE"
    assert result["task_status"] == "UNKNOWN"
    assert len(reqs) == 1 and budget.snapshot()["frozen"]
    assert not list(path.iterdir())
    assert "secret-private-key" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finish", "expected_reason", "expected_finish"),
    [
        ("length", "MODEL_OUTPUT_TRUNCATED", "LENGTH"),
        ("content_filter", "MODEL_DID_NOT_FINISH_NORMALLY", "CONTENT_FILTER"),
        ("secret-private-key", "MODEL_DID_NOT_FINISH_NORMALLY", "OTHER"),
    ],
)
async def test_finish_reason_and_usage_are_diagnostic_and_never_unfreeze(
    tmp_path, finish, expected_reason, expected_finish
):
    path = private(tmp_path)
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000)
    payload = response("敏感模型输出不可记录")
    payload["choices"][0]["finish_reason"] = finish
    result = await observe_dev_case(
        load_dataset(DATASET),
        "KNOW-020",
        credential="secret-private-key",
        budget=budget,
        review_directory=path,
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    assert result["component_status"] == "INCOMPLETE"
    assert result["reason"] == expected_reason
    assert result["provider_finish_reason"] == expected_finish
    assert result["input_tokens"] == 150 and result["output_tokens"] == 25
    assert result["provider_bill_verified"] is False
    assert budget.snapshot()["frozen"]
    assert budget.snapshot()["reserved_nano"] == 163_840_000
    assert "secret-private-key" not in json.dumps(result)
    assert "敏感模型输出" not in json.dumps(result, ensure_ascii=False)
    assert not list(path.iterdir())


@pytest.mark.asyncio
async def test_closed_budget_and_unapproved_input_never_send(tmp_path):
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000)
    budget.reserve("x", 1)
    budget.finish("x", usage_known=False)
    calls = []
    gateway = _Gateway(
        "深蹲上推时怎么呼吸？",
        "secret-key",
        budget,
        httpx.MockTransport(lambda req: calls.append(req)),
    )
    with pytest.raises(_ProbeFailure):
        await gateway.chat_with_tools(gateway.messages, tools=[{"name": "danger"}])
    with pytest.raises(_ProbeFailure):
        await gateway.chat_with_tools(gateway.messages, tools=[])
    assert calls == [] and gateway.attempts == 0


@pytest.mark.asyncio
async def test_single_gateway_cannot_repeat_or_change_messages(tmp_path):
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000)
    calls = []
    gateway = _Gateway(
        "深蹲上推时怎么呼吸？",
        "secret-key",
        budget,
        httpx.MockTransport(
            lambda req: (calls.append(req), httpx.Response(200, json=response()))[1]
        ),
    )
    with pytest.raises(_ProbeFailure):
        await gateway.chat_with_tools([{"role": "user", "content": "泄漏信息"}], tools=[])
    assert not calls
    await gateway.chat_with_tools(gateway.messages, tools=[])
    with pytest.raises(_ProbeFailure):
        await gateway.chat_with_tools(gateway.messages, tools=[])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_frozen_ledger_prevents_model_and_review_creation(tmp_path):
    path = private(tmp_path)
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000)
    budget.reserve("x", 1)
    budget.finish("x", usage_known=False)
    result = await observe_dev_case(
        load_dataset(DATASET),
        "KNOW-001",
        credential="secret-private-key",
        budget=budget,
        review_directory=path,
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(lambda _: pytest.fail("frozen ledger must block")),
    )
    assert result["task_status"] == "UNKNOWN"
    assert result["component_status"] == "INCOMPLETE"
    assert result["attempts"] == 0
    assert not list(path.iterdir())


@pytest.mark.asyncio
async def test_rejects_test_split_and_insecure_output_without_call(tmp_path):
    path = private(tmp_path)
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000)
    data = load_dataset(DATASET)
    changed = copy.deepcopy(data)
    case = next(c for c in changed.cases if c.case_id == "KNOW-001")
    object.__setattr__(case, "split", "test")
    with pytest.raises(ValueError):
        await observe_dev_case(
            changed,
            "KNOW-001",
            credential="secret-key",
            budget=budget,
            review_directory=path,
            execute=True,
            confirmed_current_pricing=True,
        )
    os.chmod(path, 0o755)
    with pytest.raises(ValueError):
        await observe_dev_case(
            data,
            "KNOW-001",
            credential="secret-key",
            budget=budget,
            review_directory=path,
            execute=True,
            confirmed_current_pricing=True,
        )
    assert budget.snapshot()["reserved_nano"] == 0


@pytest.mark.asyncio
async def test_safety_guard_observation_never_passes_automatically(tmp_path):
    path = private(tmp_path)
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000)
    result = await observe_dev_case(
        load_dataset(DATASET),
        "SAFE-013",
        credential="secret-private-key",
        budget=budget,
        review_directory=path,
        execute=True,
        confirmed_current_pricing=True,
        transport=httpx.MockTransport(lambda _: pytest.fail("guard should not invoke provider")),
    )
    assert result["task_status"] == "UNKNOWN"
    assert result["human_review"] == "NOT_REVIEWED"
    assert result["attempts"] == 0
    assert result["component_status"] == "OBSERVED"


def cli_args(tmp_path):
    key = tmp_path / "key"
    key.write_text("secret-private-key")
    key.chmod(0o600)
    private(tmp_path)
    DurableBudget(tmp_path / "budget.sqlite", limit_nano=500_000_000)
    return [
        "--case-id",
        "KNOW-001",
        "--dataset",
        str(DATASET),
        "--credential-file",
        str(key),
        "--budget-file",
        str(tmp_path / "budget.sqlite"),
        "--budget-limit-nano",
        "500000000",
        "--budget-opening-nano",
        "0",
        "--minimum-reserved-nano",
        "0",
        "--review-directory",
        str(tmp_path / "private"),
        "--report-file",
        str(tmp_path / "report.json"),
        "--execute",
        "--confirm-current-pricing",
    ]


def test_cli_precreates_private_report_and_uses_existing_budget(tmp_path, capsys):
    args = cli_args(tmp_path)

    def handler(request):
        assert (tmp_path / "report.json").exists()
        assert stat.S_IMODE((tmp_path / "report.json").stat().st_mode) == 0o600
        return httpx.Response(200, json=response("危险答案 secret-private-key"))

    assert main(args, transport=httpx.MockTransport(handler)) == 3
    stdout = capsys.readouterr().out
    report = (tmp_path / "report.json").read_text()
    assert "secret-private-key" not in report + stdout
    assert "危险答案" not in report + stdout
    assert json.loads(report)["task_status"] == "UNKNOWN"


@pytest.mark.parametrize(
    "kind", ["key_permission", "missing_budget", "existing_report", "symlink_key"]
)
def test_cli_prechecks_fail_before_request(tmp_path, kind):
    args = cli_args(tmp_path)
    if kind == "key_permission":
        (tmp_path / "key").chmod(0o644)
    elif kind == "missing_budget":
        (tmp_path / "budget.sqlite").unlink()
    elif kind == "existing_report":
        (tmp_path / "report.json").write_text("preserve")
    else:
        (tmp_path / "key").rename(tmp_path / "original")
        (tmp_path / "key").symlink_to(tmp_path / "original")
    assert (
        main(args, transport=httpx.MockTransport(lambda _: pytest.fail("must not call provider")))
        == 1
    )
    if kind == "existing_report":
        assert (tmp_path / "report.json").read_text() == "preserve"
