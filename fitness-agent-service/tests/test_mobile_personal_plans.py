from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.api.routes import mobile_training
from app.infrastructure.gateway_client import GatewayForbiddenError, GatewayUnavailableError


async def test_personal_plan_listing_paginates_and_enforces_auth(monkeypatch):
    identity = SimpleNamespace(subject="student", organization_ids=frozenset({"org"}))
    state = {"mode": "ok", "before": None}

    async def auth(request, response):
        return "ref", identity

    async def quota(request, actor):
        assert actor is identity

    class Auth:
        async def signed_context(self, ref, expected):
            assert ref == "ref" and expected is identity
            return "signed"

    class Gateway:
        async def list_personal_training_plans(self, context, before):
            assert context.signed_context == "signed"
            state["before"] = before
            if state["mode"] == "forbidden":
                raise GatewayForbiddenError("denied")
            if state["mode"] == "unavailable":
                raise GatewayUnavailableError("offline")
            return [SimpleNamespace(id=f"p{i:03}", title="计划") for i in range(51)]

    app = FastAPI()
    app.include_router(mobile_training.router)
    app.state.settings = SimpleNamespace(multimodal_training_links_enabled=True)
    app.state.gateway = Gateway()
    monkeypatch.setattr(mobile_training, "_auth", auth)
    monkeypatch.setattr(mobile_training, "auth_service", lambda request: Auth())
    monkeypatch.setattr(mobile_training, "enforce_chat_quota", quota)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = "/api/v1/mobile/agent/training/plans"
        result = await client.get(path, params={"before": "p100"})
        assert result.status_code == 200 and state["before"] == "p100"
        assert len(result.json()["plans"]) == 50
        assert result.json()["next_cursor"] == "p049"
        assert (await client.get(path, params={"before": "../wrong"})).status_code == 422
        state["mode"] = "forbidden"
        assert (await client.get(path)).status_code == 403
        state["mode"] = "unavailable"
        assert (await client.get(path)).status_code == 503
        app.state.settings.multimodal_training_links_enabled = False
        assert (await client.get(path)).status_code == 503
