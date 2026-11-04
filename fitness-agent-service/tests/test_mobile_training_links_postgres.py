"""Authorization and deletion behavior against an explicitly disposable database."""

from dataclasses import replace
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from sqlalchemy import text

from app.api.routes import mobile_training
from app.infrastructure.gateway_client import GatewayForbiddenError
from app.media.deletion import MediaDeletionRepository
from tests import test_multimodal_dispatch_postgres as pg
from tests.test_ownership_recovery_postgres import build

pytestmark = pg.pytestmark
isolate_test_data = pg.isolate_test_data


def make_plan(identity):
    item = SimpleNamespace(id="item-1", exercise_name="坐姿划船")
    day = SimpleNamespace(id="day-1", title="训练日", items=[item])
    return SimpleNamespace(
        id="plan-1",
        title="我的计划",
        version=1,
        days=[day],
        student_id=identity.subject,
        organization_id="org-1",
        status="PUBLISHED",
    )


async def test_training_link_requires_both_authorities_and_disappears_on_asset_deletion(
    monkeypatch,
):
    db = pg.database()
    try:
        cipher, _repo, old, _new, _conversation, run, asset, _client = await build(db)
        active = [old]
        plan = make_plan(old)
        denied = [False]
        calls = []

        class Auth:
            async def signed_context(self, ref, expected):
                assert expected == active[0]
                return "server-signed-context"

        class Gateway:
            async def get_training_plan(self, context, plan_id):
                assert context.signed_context == "server-signed-context"
                calls.append(plan_id)
                if denied[0]:
                    raise GatewayForbiddenError("denied")
                return plan

            async def list_training_day_executions(self, context, plan_id):
                assert context.signed_context == "server-signed-context"
                return [
                    SimpleNamespace(
                        plan_id=plan_id,
                        day_id="day-1",
                        student_id=old.subject,
                        organization_id="org-1",
                        status="COMPLETED",
                        execution_date="2026-11-04",
                    ),
                    SimpleNamespace(
                        plan_id=plan_id,
                        day_id="day-1",
                        student_id="other",
                        organization_id="org-1",
                        status="COMPLETED",
                        execution_date="2026-11-03",
                    ),
                ]

        async def auth(request, response):
            return "ref", active[0]

        async def quota(request, identity):
            return None

        app = FastAPI()
        app.include_router(mobile_training.router)
        app.state.database = db
        app.state.gateway = Gateway()
        app.state.settings = SimpleNamespace(multimodal_training_links_enabled=True)
        monkeypatch.setattr(mobile_training, "_auth", auth)
        monkeypatch.setattr(mobile_training, "auth_service", lambda request: Auth())
        monkeypatch.setattr(mobile_training, "enforce_chat_quota", quota)
        payload = {"plan_id": "plan-1", "day_id": "day-1", "item_id": "item-1"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            base = "/api/v1/mobile/agent"
            app.state.settings.multimodal_training_links_enabled = False
            assert (await client.get(f"{base}/training/plans/plan-1")).status_code == 503
            app.state.settings.multimodal_training_links_enabled = True
            assert (await client.get(f"{base}/training/plans/plan-1")).json()["days"][0]["items"][
                0
            ]["item_id"] == "item-1"
            assert (await client.put(f"{base}/runs/{run}/training-link", json=payload)).json() == {
                "linked": True
            }
            assert (await client.get(f"{base}/runs/{run}/training-link")).json()[
                "exercise_name"
            ] == "坐姿划船"
            linked_reports = (await client.get(f"{base}/training/plans/plan-1/reports")).json()[
                "reports"
            ]
            assert len(linked_reports) == 1 and linked_reports[0]["run_id"] == run
            records = (await client.get(f"{base}/runs/{run}/training-executions")).json()[
                "executions"
            ]
            assert records == [{"status": "COMPLETED", "execution_date": "2026-11-04"}]
            plan.status = "DRAFT"
            assert (await client.get(f"{base}/runs/{run}/training-link")).status_code == 404
            plan.status = "PUBLISHED"
            plan.organization_id = "other-org"
            assert (
                await client.put(f"{base}/runs/{run}/training-link", json=payload)
            ).status_code == 404
            plan.organization_id = "org-1"
            assert (
                await client.put(
                    f"{base}/runs/{run}/training-link", json={**payload, "item_id": "unknown"}
                )
            ).status_code == 409
            active[0] = replace(old, subject="other")
            assert (await client.get(f"{base}/training/plans/plan-1/reports")).status_code == 404
            assert (await client.get(f"{base}/runs/{run}/training-link")).status_code == 404
            assert (
                await client.put(f"{base}/runs/{run}/training-link", json=payload)
            ).status_code == 404
            active[0] = old
            denied[0] = True
            assert (await client.get(f"{base}/runs/{run}/training-link")).status_code == 404
            # User can remove their own association even if access to plan was revoked.
            assert (await client.delete(f"{base}/runs/{run}/training-link")).json() == {
                "linked": False
            }
            denied[0] = False
            assert (
                await client.put(f"{base}/runs/{run}/training-link", json=payload)
            ).status_code == 200
            # Run expiry must not prevent report and link invalidation on source deletion.
            async with db.engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM agent_chat_runs WHERE run_id=:run"), {"run": run}
                )
            await MediaDeletionRepository(db, cipher).request(old, asset)
            assert (await client.get(f"{base}/runs/{run}/training-link")).status_code == 404
            assert (await client.get(f"{base}/training/plans/plan-1/reports")).json()[
                "reports"
            ] == []
            async with db.engine.connect() as conn:
                report = (
                    await conn.execute(
                        text(
                            "SELECT deleted_at,report_ciphertext FROM agent_movement_reports WHERE run_id=:run"
                        ),
                        {"run": run},
                    )
                ).one()
                count = (
                    await conn.execute(text("SELECT count(*) FROM agent_report_training_links"))
                ).scalar_one()
                messages = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM agent_chat_messages WHERE run_id=:run AND content_ciphertext IS NOT NULL"
                        ),
                        {"run": run},
                    )
                ).scalar_one()
            assert report.deleted_at is not None and report.report_ciphertext is None
            assert count == 0 and messages == 0
            assert calls
    finally:
        await db.close()
