"""Read-only links from personal analysis reports to authorized training items.

The training service remains the authority for plan access and status. Links do
not create training executions and never mutate a training plan.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.api.chat_protection import enforce_chat_quota
from app.api.routes.mobile_auth import service as auth_service
from app.api.routes.mobile_media import _auth
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import (
    GatewayClientError,
    GatewayForbiddenError,
    GatewayNotFoundError,
    GatewayRequestContext,
)
from app.streaming.control import run_owner

router = APIRouter(prefix="/api/v1/mobile/agent", tags=["mobile-training-links"])
_ID = r"^[A-Za-z0-9_-]{1,128}$"


class TrainingLinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(pattern=_ID)
    day_id: str = Field(pattern=_ID)
    item_id: str = Field(pattern=_ID)


def _enabled(request: Request) -> None:
    if not getattr(request.app.state.settings, "multimodal_training_links_enabled", False):
        raise HTTPException(503, "训练计划关联暂未启用")


async def _plan(request: Request, ref: str, identity: AgentIdentity, plan_id: str):
    if not re.fullmatch(_ID, plan_id):
        raise HTTPException(404, "训练计划不存在或无法访问")
    signed = await auth_service(request).signed_context(ref, identity)
    gateway = getattr(request.app.state, "gateway", None)
    if gateway is None:
        raise HTTPException(503, "训练服务暂时不可用")
    request_id = str(uuid4())
    try:
        plan = await gateway.get_training_plan(
            GatewayRequestContext(
                signed_context=signed, request_id=request_id, trace_id=request_id
            ),
            plan_id,
        )
    except (GatewayForbiddenError, GatewayNotFoundError):
        raise HTTPException(404, "训练计划不存在或无法访问") from None
    except GatewayClientError:
        raise HTTPException(503, "训练服务暂时不可用") from None
    # Even privileged coaches/admins cannot attach their own report to another person's plan.
    if (
        plan.id != plan_id
        or plan.student_id != identity.subject
        or plan.organization_id not in identity.organization_ids
        or plan.status != "PUBLISHED"
    ):
        raise HTTPException(404, "训练计划不存在或无法访问")
    return plan


def _item(plan, day_id: str, item_id: str):
    for day in plan.days:
        if day.id == day_id:
            for item in day.items:
                if item.id == item_id:
                    return day, item
    raise HTTPException(409, "训练计划已变化，请重新选择动作")


async def _report(conn, run_id: str, owner: str, *, lock: bool = False):
    # Lock the conversation first, matching media deletion and publication.
    conversation = (
        await conn.execute(
            text(
                """SELECT c.conversation_id FROM agent_chat_conversations c
        JOIN agent_movement_reports r ON r.conversation_id=c.conversation_id
        WHERE r.run_id=:run AND r.owner_hash=:owner AND c.owner_hash=:owner
          AND c.deleted_at IS NULL AND r.deleted_at IS NULL AND r.report_ciphertext IS NOT NULL
          AND r.schema_version='movement-analysis.v1'"""
                + (" FOR UPDATE OF c" if lock else "")
            ),
            {"run": run_id, "owner": owner},
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(404, "动作报告不存在或不可访问")
    row = (
        await conn.execute(
            text(
                """SELECT report_id FROM agent_movement_reports
        WHERE run_id=:run AND conversation_id=:conversation AND owner_hash=:owner
          AND deleted_at IS NULL AND report_ciphertext IS NOT NULL
          AND schema_version='movement-analysis.v1'"""
                + (" FOR UPDATE" if lock else "")
            ),
            {"run": run_id, "owner": owner, "conversation": conversation},
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "动作报告不存在或不可访问")
    return str(row)


@router.get("/training/plans")
async def personal_training_plans(
    request: Request, response: Response, before: str | None = None
) -> dict[str, Any]:
    ref, identity = await _auth(request, response)
    _enabled(request)
    await enforce_chat_quota(request, identity)
    if before is not None and not re.fullmatch(_ID, before):
        raise HTTPException(422, "分页标识无效")
    signed = await auth_service(request).signed_context(ref, identity)
    gateway = getattr(request.app.state, "gateway", None)
    if gateway is None:
        raise HTTPException(503, "训练服务暂时不可用")
    request_id = str(uuid4())
    try:
        plans = await gateway.list_personal_training_plans(
            GatewayRequestContext(
                signed_context=signed, request_id=request_id, trace_id=request_id
            ),
            before,
        )
    except GatewayForbiddenError:
        raise HTTPException(403, "当前账号无法查看个人训练计划") from None
    except GatewayClientError:
        raise HTTPException(503, "训练服务暂时不可用") from None
    if len(plans) > 51:
        raise HTTPException(503, "训练服务返回异常")
    return {
        "plans": [{"plan_id": p.id, "title": p.title} for p in plans[:50]],
        "next_cursor": plans[49].id if len(plans) > 50 else None,
    }


@router.get("/training/plans/{plan_id}")
async def training_plan(plan_id: str, request: Request, response: Response) -> dict[str, Any]:
    ref, identity = await _auth(request, response)
    _enabled(request)
    await enforce_chat_quota(request, identity)
    plan = await _plan(request, ref, identity, plan_id)
    return {
        "plan_id": plan.id,
        "title": plan.title,
        "version": plan.version,
        "days": [
            {
                "day_id": day.id,
                "title": day.title,
                "items": [
                    {"item_id": item.id, "exercise_name": item.exercise_name} for item in day.items
                ],
            }
            for day in plan.days
        ],
    }


@router.get("/training/plans/{plan_id}/reports")
async def reports_for_plan(
    plan_id: str, request: Request, response: Response, before: UUID | None = None
) -> dict[str, Any]:
    ref, identity = await _auth(request, response)
    _enabled(request)
    await enforce_chat_quota(request, identity)
    plan = await _plan(request, ref, identity, plan_id)
    owner = run_owner(identity)
    async with request.app.state.database.engine.connect() as conn:
        anchor = None
        if before:
            anchor = (
                await conn.execute(
                    text("""SELECT r.created_at FROM agent_movement_reports r
                JOIN agent_report_training_links l ON l.report_id=r.report_id
                JOIN agent_chat_conversations c ON c.conversation_id=r.conversation_id
                WHERE r.report_id=:id AND l.plan_id=:plan AND r.owner_hash=:owner
                  AND c.owner_hash=:owner AND c.deleted_at IS NULL AND r.deleted_at IS NULL"""),
                    {"id": str(before), "plan": plan_id, "owner": owner},
                )
            ).scalar_one_or_none()
            if anchor is None:
                raise HTTPException(422, "分页标识已失效，请刷新")
        rows = (
            (
                await conn.execute(
                    text("""SELECT r.report_id,r.run_id,r.conversation_id,r.created_at,l.day_id,l.item_id
            FROM agent_report_training_links l JOIN agent_movement_reports r ON r.report_id=l.report_id
            JOIN agent_chat_conversations c ON c.conversation_id=r.conversation_id
            WHERE l.plan_id=:plan AND r.owner_hash=:owner AND c.owner_hash=:owner
              AND c.deleted_at IS NULL AND r.deleted_at IS NULL AND r.report_ciphertext IS NOT NULL
              AND (CAST(:anchor AS timestamptz) IS NULL OR (r.created_at,r.report_id)<(:anchor,:before))
            ORDER BY r.created_at DESC,r.report_id DESC LIMIT 101"""),
                    {
                        "plan": plan_id,
                        "owner": owner,
                        "anchor": anchor,
                        "before": str(before) if before else None,
                    },
                )
            )
            .mappings()
            .all()
        )
    valid = {(day.id, item.id) for day in plan.days for item in day.items}
    items = [
        {
            "run_id": r["run_id"],
            "conversation_id": r["conversation_id"],
            "day_id": r["day_id"],
            "item_id": r["item_id"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows[:100]
        if (r["day_id"], r["item_id"]) in valid
    ]
    return {
        "reports": items,
        "next_cursor": str(rows[99]["report_id"]) if len(rows) > 100 else None,
    }


@router.put("/runs/{run_id}/training-link")
async def put_training_link(
    run_id: UUID, payload: TrainingLinkInput, request: Request, response: Response
) -> dict[str, bool]:
    ref, identity = await _auth(request, response)
    _enabled(request)
    await enforce_chat_quota(request, identity)
    plan = await _plan(request, ref, identity, payload.plan_id)
    _item(plan, payload.day_id, payload.item_id)
    # Recheck signed identity immediately before the transaction.
    await auth_service(request).signed_context(ref, identity)
    async with request.app.state.database.engine.begin() as conn:
        report_id = await _report(conn, str(run_id), run_owner(identity), lock=True)
        await conn.execute(
            text("""INSERT INTO agent_report_training_links (report_id,plan_id,day_id,item_id)
          VALUES (:report,:plan,:day,:item) ON CONFLICT (report_id) DO UPDATE
          SET plan_id=EXCLUDED.plan_id,day_id=EXCLUDED.day_id,item_id=EXCLUDED.item_id,
              updated_at=CURRENT_TIMESTAMP"""),
            {
                "report": report_id,
                "plan": payload.plan_id,
                "day": payload.day_id,
                "item": payload.item_id,
            },
        )
    return {"linked": True}


@router.get("/runs/{run_id}/training-link")
async def get_training_link(run_id: UUID, request: Request, response: Response) -> dict[str, Any]:
    ref, identity = await _auth(request, response)
    _enabled(request)
    await enforce_chat_quota(request, identity)
    owner = run_owner(identity)
    async with request.app.state.database.engine.connect() as conn:
        report_id = await _report(conn, str(run_id), owner)
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT plan_id,day_id,item_id FROM agent_report_training_links WHERE report_id=:id"
                    ),
                    {"id": report_id},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        return {"linked": False}
    plan = await _plan(request, ref, identity, row["plan_id"])
    day, item = _item(plan, row["day_id"], row["item_id"])
    # Avoid returning a link if the source report was concurrently deleted.
    async with request.app.state.database.engine.connect() as conn:
        if await _report(conn, str(run_id), owner) != report_id:
            raise HTTPException(404, "动作报告不存在或不可访问")
    return {
        "linked": True,
        "plan_id": plan.id,
        "plan_title": plan.title,
        "day_id": day.id,
        "day_title": day.title,
        "item_id": item.id,
        "exercise_name": item.exercise_name,
    }


@router.delete("/runs/{run_id}/training-link")
async def delete_training_link(
    run_id: UUID, request: Request, response: Response
) -> dict[str, bool]:
    _, identity = await _auth(request, response)
    _enabled(request)
    await enforce_chat_quota(request, identity)
    async with request.app.state.database.engine.begin() as conn:
        report_id = await _report(conn, str(run_id), run_owner(identity), lock=True)
        await conn.execute(
            text("DELETE FROM agent_report_training_links WHERE report_id=:id"), {"id": report_id}
        )
    return {"linked": False}


@router.get("/runs/{run_id}/training-executions")
async def report_training_executions(
    run_id: UUID, request: Request, response: Response
) -> dict[str, Any]:
    ref, identity = await _auth(request, response)
    _enabled(request)
    await enforce_chat_quota(request, identity)
    owner = run_owner(identity)
    async with request.app.state.database.engine.connect() as conn:
        report_id = await _report(conn, str(run_id), owner)
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT plan_id,day_id,item_id FROM agent_report_training_links WHERE report_id=:id"
                    ),
                    {"id": report_id},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(404, "尚未关联训练计划")
    plan = await _plan(request, ref, identity, row["plan_id"])
    _item(plan, row["day_id"], row["item_id"])
    signed = await auth_service(request).signed_context(ref, identity)
    request_id = str(uuid4())
    try:
        executions = await request.app.state.gateway.list_training_day_executions(
            GatewayRequestContext(
                signed_context=signed, request_id=request_id, trace_id=request_id
            ),
            plan.id,
        )
    except (GatewayForbiddenError, GatewayNotFoundError):
        raise HTTPException(404, "训练记录不存在或无法访问") from None
    except GatewayClientError:
        raise HTTPException(503, "训练服务暂时不可用") from None
    async with request.app.state.database.engine.connect() as conn:
        await _report(conn, str(run_id), owner)
        current = (
            (
                await conn.execute(
                    text(
                        "SELECT plan_id,day_id,item_id FROM agent_report_training_links WHERE report_id=:id"
                    ),
                    {"id": report_id},
                )
            )
            .mappings()
            .first()
        )
        if current is None or dict(current) != dict(row):
            raise HTTPException(409, "训练关联已变化，请刷新")
    return {
        "executions": [
            {"status": item.status, "execution_date": item.execution_date}
            for item in executions
            if item.plan_id == plan.id
            and item.day_id == row["day_id"]
            and item.student_id == identity.subject
            and item.organization_id in identity.organization_ids
        ]
    }
