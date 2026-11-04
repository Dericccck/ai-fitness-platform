"""Rollout gate and wire compatibility for the new knowledge analysis mode."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
from fastapi import FastAPI

from app.api.routes import mobile_media
from app.infrastructure.agent_context import AgentIdentity


async def test_knowledge_mode_is_disabled_until_explicitly_enabled(monkeypatch):
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        multimodal_knowledge_enabled=False, multimodal_model="m", multimodal_prompt_version="v1"
    )
    calls = []

    class Repository:
        async def submit(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                run_id=kwargs["run_id"], message_id="m", created=True, content_deleted=False
            )

    app.state.multimodal_repository = Repository()
    app.include_router(mobile_media.router)

    async def auth(request, response):
        return "a" * 64, AgentIdentity(
            "user", frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999
        )

    async def quota(request, identity):
        return None

    monkeypatch.setattr(mobile_media, "_auth", auth)
    monkeypatch.setattr(mobile_media, "enforce_chat_quota", quota)
    payload = {
        "conversation_id": "c",
        "run_id": str(uuid4()),
        "client_message_id": str(uuid4()),
        "message": "解读",
        "media_ids": [str(uuid4())],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/mobile/agent/capabilities")).json()[
            "knowledge_analysis_enabled"
        ] is False
        assert (
            await client.post(
                "/api/v1/mobile/agent/runs", json={**payload, "analysis_mode": "knowledge"}
            )
        ).status_code == 503
        assert not calls
        assert (await client.post("/api/v1/mobile/agent/runs", json=payload)).status_code == 202
        assert "analysis_mode" not in calls[-1]
        app.state.settings.multimodal_knowledge_enabled = True
        assert (
            await client.post(
                "/api/v1/mobile/agent/runs", json={**payload, "analysis_mode": "knowledge"}
            )
        ).status_code == 202
        assert calls[-1]["analysis_mode"] == "knowledge"
        assert (
            await client.post(
                "/api/v1/mobile/agent/runs", json={**payload, "analysis_mode": "other"}
            )
        ).status_code == 422
