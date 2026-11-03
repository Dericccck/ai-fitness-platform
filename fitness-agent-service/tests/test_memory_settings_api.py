from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes.memories import router
from app.infrastructure.agent_context import AgentContextVerificationError, AgentIdentity
from app.memory.models import validate_memory_owner
from app.memory.repository import MemorySettings, MemoryVersionConflictError


class Verifier:
    def verify(self, token: str) -> AgentIdentity:
        if token != "signed":
            raise AgentContextVerificationError("invalid")
        return AgentIdentity("user-1", frozenset({"org-1"}), frozenset(), 1, 2)


class Service:
    def __init__(self) -> None:
        self.settings = MemorySettings()

    async def get_settings(
        self, *, identity: AgentIdentity, organization_id: str
    ) -> MemorySettings:
        validate_memory_owner(identity, organization_id)
        return self.settings

    async def update_settings(
        self,
        *,
        identity: AgentIdentity,
        organization_id: str,
        expected_revision: int,
        extraction_enabled: bool | None,
        personalization_enabled: bool | None,
    ) -> MemorySettings:
        validate_memory_owner(identity, organization_id)
        if expected_revision != self.settings.revision:
            raise MemoryVersionConflictError()
        extraction = (
            self.settings.extraction_enabled if extraction_enabled is None else extraction_enabled
        )
        personalization = (
            self.settings.personalization_enabled
            if personalization_enabled is None
            else personalization_enabled
        )
        changed = (extraction, personalization) != (
            self.settings.extraction_enabled,
            self.settings.personalization_enabled,
        )
        self.settings = MemorySettings(
            extraction, personalization, self.settings.revision + int(changed)
        )
        return self.settings


def app_for_test(*, extraction_config_enabled: bool = True) -> FastAPI:
    app = FastAPI()
    app.state.context_verifier = Verifier()
    app.state.memory_service = Service()
    app.state.settings = SimpleNamespace(memory_async_extraction_enabled=extraction_config_enabled)
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_settings_access_validation_and_versioning() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_for_test()), base_url="http://test"
    ) as client:
        url = "/api/v1/agent/memories/settings?organization_id=org-1"
        headers = {"X-Agent-Context": "signed"}
        assert (await client.get(url)).status_code == 401
        assert (await client.get(url, headers={"X-Agent-Context": "bad"})).status_code == 401
        assert (await client.get(url.replace("org-1", "org-2"), headers=headers)).status_code == 403
        assert (await client.get(url, headers=headers)).json() == {
            "extraction_enabled": True,
            "personalization_enabled": True,
            "revision": 0,
            "extraction_config_enabled": True,
        }
        response = await client.patch(
            url,
            headers=headers,
            json={
                "expected_revision": 0,
                "extraction_enabled": False,
            },
        )
        assert response.status_code == 200
        assert response.json() == {
            "extraction_enabled": False,
            "personalization_enabled": True,
            "revision": 1,
            "extraction_config_enabled": True,
        }
        assert (
            await client.patch(
                url,
                headers=headers,
                json={
                    "expected_revision": 0,
                    "personalization_enabled": False,
                },
            )
        ).status_code == 409
        assert (
            await client.patch(url, headers=headers, json={"expected_revision": 1})
        ).status_code == 422


@pytest.mark.asyncio
async def test_settings_distinguish_user_preference_from_service_configuration() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app_for_test(extraction_config_enabled=False)),
        base_url="http://test",
    ) as client:
        url = "/api/v1/agent/memories/settings?organization_id=org-1"
        headers = {"X-Agent-Context": "signed"}
        result = await client.get(url, headers=headers)
        assert result.json()["extraction_enabled"] is True
        assert result.json()["extraction_config_enabled"] is False
        result = await client.patch(
            url, headers=headers, json={"expected_revision": 0, "personalization_enabled": False}
        )
        assert result.json()["extraction_enabled"] is True
        assert result.json()["extraction_config_enabled"] is False
        assert (
            await client.patch(
                url,
                headers=headers,
                json={
                    "expected_revision": 1,
                    "extraction_enabled": "false",
                },
            )
        ).status_code == 422
        assert (
            await client.patch(
                url,
                headers=headers,
                json={
                    "expected_revision": 1,
                    "subject_user_id": "user-2",
                    "extraction_enabled": False,
                },
            )
        ).status_code == 422
