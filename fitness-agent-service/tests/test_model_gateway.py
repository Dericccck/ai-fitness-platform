from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.infrastructure.model_gateway import (
    ModelConfigurationError,
    ModelGateway,
    ModelResponseError,
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
    create.assert_awaited_once()


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
