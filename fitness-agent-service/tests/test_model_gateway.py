from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openai import OpenAIError
from prometheus_client import generate_latest

from app.core.config import Settings
from app.core.metrics import HttpMetrics
from app.infrastructure.model_gateway import (
    ModelConfigurationError,
    ModelGateway,
    ModelResponseError,
    _provider_status_code,
)


def configured_settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_api_key="llm-key",
        llm_model="fitness-model",
    )


def response_for(*, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            function=SimpleNamespace(
                                name="fitness.course.list.v1",
                                arguments=arguments,
                            ),
                        )
                    ],
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )


async def test_model_gateway_normalizes_openai_tool_call() -> None:
    gateway = ModelGateway(configured_settings())
    create = AsyncMock(return_value=response_for(arguments='{"organization_id":"org-1"}'))
    gateway._llm.chat.completions.create = create

    turn = await gateway.chat_with_tools(
        [{"role": "user", "content": "查询课程"}],
        tools=[{"type": "function", "function": {"name": "fitness.course.list.v1"}}],
    )

    assert turn.content == ""
    assert turn.tool_calls[0].name == "fitness.course.list.v1"
    assert turn.tool_calls[0].arguments == {"organization_id": "org-1"}
    assert turn.input_tokens == 11
    assert turn.output_tokens == 7
    assert create.await_args.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    create.assert_awaited_once()


async def test_model_gateway_can_force_a_specific_tool_for_explicit_write_intent() -> None:
    gateway = ModelGateway(configured_settings())
    create = AsyncMock(return_value=response_for(arguments='{"course_id":"course-1"}'))
    gateway._llm.chat.completions.create = create

    await gateway.chat_with_tools(
        [{"role": "user", "content": "创建预约"}],
        tools=[{"type": "function", "function": {"name": "fitness_booking_create_v1"}}],
        force_tool_name="fitness_booking_create_v1",
    )

    assert create.await_args.kwargs["tool_choice"] == {
        "type": "function",
        "function": {"name": "fitness_booking_create_v1"},
    }


async def test_model_gateway_supports_task_specific_tool_call_output_budget() -> None:
    gateway = ModelGateway(configured_settings())
    create = AsyncMock(return_value=response_for(arguments='{"course_id":"course-1"}'))
    gateway._llm.chat.completions.create = create

    await gateway.chat_with_tools(
        [{"role": "user", "content": "创建训练计划草案"}],
        tools=[{"type": "function", "function": {"name": "fitness_training_plan_create_v1"}}],
        max_output_tokens=3000,
    )

    assert create.await_args.kwargs["max_tokens"] == 3000


async def test_model_gateway_requests_json_object_for_structured_generation() -> None:
    gateway = ModelGateway(configured_settings())
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"title":"力量计划"}'))]
    )
    create = AsyncMock(return_value=response)
    gateway._llm.chat.completions.create = create

    result = await gateway.chat_json([{"role": "user", "content": "生成计划"}])

    assert result == '{"title":"力量计划"}'
    assert create.await_args.kwargs["response_format"] == {"type": "json_object"}


async def test_model_gateway_supports_task_specific_json_output_budget() -> None:
    gateway = ModelGateway(configured_settings())
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
    )
    create = AsyncMock(return_value=response)
    gateway._llm.chat.completions.create = create

    await gateway.chat_json(
        [{"role": "user", "content": "生成训练计划"}],
        max_output_tokens=3000,
    )

    assert create.await_args.kwargs["max_tokens"] == 3000


async def test_model_gateway_rejects_non_object_tool_arguments() -> None:
    gateway = ModelGateway(configured_settings())
    gateway._llm.chat.completions.create = AsyncMock(return_value=response_for(arguments="[]"))

    with pytest.raises(ModelResponseError):
        await gateway.chat_with_tools(
            [{"role": "user", "content": "查询课程"}],
            tools=[{"type": "function", "function": {"name": "tool"}}],
        )


async def test_model_gateway_does_not_call_provider_when_unconfigured() -> None:
    gateway = ModelGateway(Settings(_env_file=None))
    create = AsyncMock()
    gateway._llm.chat.completions.create = create

    with pytest.raises(ModelConfigurationError):
        await gateway.chat_with_tools(
            [{"role": "user", "content": "查询课程"}],
            tools=[{"type": "function", "function": {"name": "tool"}}],
        )

    create.assert_not_awaited()


async def test_model_gateway_converts_provider_errors_to_stable_response_error() -> None:
    metrics = HttpMetrics.create(service_name="test", service_version="test", environment="test")
    gateway = ModelGateway(configured_settings(), metrics=metrics)
    gateway._llm.chat.completions.create = AsyncMock(side_effect=OpenAIError("provider failed"))

    with pytest.raises(ModelResponseError, match="LLM 服务请求失败"):
        await gateway.chat_with_tools(
            [{"role": "user", "content": "查询经营指标"}],
            tools=[{"type": "function", "function": {"name": "fitness_operations_v1"}}],
        )

    exposition = generate_latest(metrics.registry).decode()
    assert 'kind="tool_calling",status="FAILED"' in exposition


def test_model_gateway_only_exposes_valid_provider_status_codes() -> None:
    assert _provider_status_code(OpenAIError("failed")) is None


async def test_local_embedding_warmup_runs_minimal_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = ModelGateway(
        Settings(
            _env_file=None,
            embedding_backend="local",
            embedding_model_path="/models/bge-m3",
        )
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(gateway, "_embed_local", lambda texts: calls.append(texts) or [[0.1]])

    await gateway.warmup_local_embedding()

    assert calls == [["健身检索模型预热"]]


async def test_remote_embedding_warmup_does_not_call_provider() -> None:
    gateway = ModelGateway(configured_settings())
    gateway._embedding.embeddings.create = AsyncMock()

    await gateway.warmup_local_embedding()

    gateway._embedding.embeddings.create.assert_not_awaited()
