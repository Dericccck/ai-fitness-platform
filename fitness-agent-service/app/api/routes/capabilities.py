"""按签名角色返回健身 Agent 的可用能力目录。

该接口服务于管理端、教练端和学员端的菜单/按钮初始化。能力来源于启动时已经完成
注册的 Tool Registry，而不是在接口里维护第二套容易漂移的权限列表。接口只返回
当前签名身份允许看到的能力元数据，不返回业务数据，也不因此授予任何额外权限；
真正的工具角色校验、确认凭证校验和 Java Gateway 资源授权仍在执行入口再次完成。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from app.infrastructure.agent_context import AgentContextVerificationError, AgentIdentity

router = APIRouter(prefix="/api/v1/agent", tags=["capabilities"])

_ROLE_ORDER = {
    "SYSTEM_ADMIN": 0,
    "ORGANIZATION_ADMIN": 1,
    "COACH": 2,
    "STUDENT": 3,
}

# Tool Registry 的 description 负责技术契约；这里补充稳定的业务分组和中文显示名，
# 让前端不需要根据工具 ID 猜菜单文案。新增工具没有补充映射时仍能返回安全的默认名，
# 但会落入 OTHER 分组，提醒后续开发者补齐产品化文案，而不会因为漏配而隐藏权限。
_DISPLAY_NAMES = {
    "fitness.user.get_current.v1": "查看我的健身资料",
    "fitness.organization.get.v1": "查看机构资料",
    "fitness.course.list.v1": "查看课程",
    "fitness.contract.list.v1": "查看合同与剩余课时",
    "fitness.appointment.list.v1": "查看预约记录",
    "fitness.booking.availability.check.v1": "检查预约可用性",
    "fitness.booking.create.v1": "创建预约",
    "fitness.booking.reschedule.v1": "调整预约",
    "fitness.booking.cancel.v1": "取消预约",
    "fitness.training.plan.get.v1": "查看训练计划",
    "fitness.training.plan.generate_draft.v1": "生成训练计划草案预览",
    "fitness.training.plan.create_draft.v1": "创建训练计划草案",
    "fitness.training.plan.submit_review.v1": "提交训练计划审核",
    "fitness.training.plan.review.v1": "审核训练计划",
    "fitness.training.plan.publish.v1": "发布训练计划",
    "fitness.training.day.executions.list.v1": "查看训练执行记录",
    "fitness.training.day.record_execution.v1": "记录训练日执行结果",
    "fitness.support.ticket.list.v1": "查看客服工单",
    "fitness.support.ticket.get.v1": "查看客服工单详情",
    "fitness.support.ticket.create.v1": "提交客服工单",
    "fitness.memory.list.v1": "查看已确认的 Memory",
    "fitness.memory.save.v1": "保存或更新 Memory",
    "fitness.memory.revoke.v1": "撤销 Memory",
    "fitness.operations.metric.query.v1": "查询经营指标",
}


class CapabilityItemResponse(BaseModel):
    """前端展示一项能力所需的最小元数据。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    domain: str
    description: str
    allowed_roles: tuple[str, ...]
    read_only: bool
    requires_confirmation: bool
    confirmation_action: str | None


class CapabilityCatalogResponse(BaseModel):
    """当前签名身份可用的能力目录，不包含组织业务数据。"""

    model_config = ConfigDict(extra="forbid")

    catalog_version: str
    roles: tuple[str, ...]
    items: tuple[CapabilityItemResponse, ...]


@router.get("/capabilities", response_model=CapabilityCatalogResponse)
async def get_capabilities(
    request: Request,
    response: Response,
    x_agent_context: str | None = Header(default=None),
    if_none_match: str | None = Header(default=None),
) -> CapabilityCatalogResponse | Response:
    """返回当前角色可以使用的健身能力。

    这里按当前签名角色过滤，而不是把全部后台能力暴露给学员再依赖前端隐藏按钮。
    前端隐藏按钮只是体验层逻辑；用户绕过前端直接调用工具时，Tool Registry 和
    Gateway 仍会再次拒绝越权请求。
    """

    identity = _verify_identity(request, x_agent_context)
    items = _visible_items(request.app.state.tool_registry.public_specs(), identity.roles)
    catalog_version = _catalog_version(items, identity.roles)
    etag = f'"{catalog_version}"'
    cache_headers = {
        "ETag": etag,
        # 版本包含角色集合，避免同一浏览器切换角色后复用另一角色的目录。
        "Cache-Control": "private, max-age=300, must-revalidate",
    }
    if _if_none_match_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)
    response.headers.update(cache_headers)
    return CapabilityCatalogResponse(
        catalog_version=catalog_version,
        roles=tuple(sorted(identity.roles, key=lambda role: (_ROLE_ORDER.get(role, 99), role))),
        items=tuple(items),
    )


def _verify_identity(request: Request, token: str | None) -> AgentIdentity:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="signed agent context is required"
        )
    try:
        return cast(AgentIdentity, request.app.state.context_verifier.verify(token))
    except AgentContextVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signed agent context"
        ) from exc


def _visible_items(
    specs: list[dict[str, Any]], roles: frozenset[str]
) -> list[CapabilityItemResponse]:
    """只返回当前角色实际可用的工具能力，并按稳定 ID 排序。"""

    visible: list[CapabilityItemResponse] = []
    for spec in sorted(specs, key=lambda item: str(item.get("name", ""))):
        allowed_roles = tuple(
            sorted(
                (str(role) for role in spec.get("allowed_roles", [])),
                key=lambda role: (_ROLE_ORDER.get(role, 99), role),
            )
        )
        if not set(allowed_roles).intersection(roles):
            continue
        tool_id = str(spec["name"])
        visible.append(
            CapabilityItemResponse(
                id=tool_id,
                display_name=_DISPLAY_NAMES.get(tool_id, tool_id),
                domain=_domain_for_tool(tool_id),
                description=str(spec["description"]),
                allowed_roles=allowed_roles,
                read_only=bool(spec["read_only"]),
                requires_confirmation=bool(spec["requires_confirmation"]),
                confirmation_action=cast(str | None, spec.get("confirmation_action")),
            )
        )
    return visible


def _domain_for_tool(tool_id: str) -> str:
    """把工具命名空间转换成前端稳定的健身业务分组。"""

    if tool_id.startswith("fitness.training."):
        return "TRAINING"
    if tool_id.startswith("fitness.booking.") or tool_id == "fitness.appointment.list.v1":
        return "BOOKING"
    if tool_id.startswith("fitness.memory."):
        return "MEMORY"
    if tool_id.startswith("fitness.operations."):
        return "OPERATIONS"
    if tool_id.startswith("fitness.support."):
        return "CUSTOMER_SERVICE"
    if tool_id.startswith("fitness."):
        return "BUSINESS"
    return "OTHER"


def _catalog_version(items: list[CapabilityItemResponse], roles: frozenset[str]) -> str:
    """对目录和角色集合做版本化，供前端安全缓存和审计定位。"""

    payload = {
        "roles": sorted(roles),
        "items": [item.model_dump(mode="json") for item in items],
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


def _if_none_match_matches(if_none_match: str | None, etag: str) -> bool:
    """判断逗号分隔的 ETag（含弱 ETag）是否命中。"""

    if not if_none_match:
        return False
    return any(
        candidate.strip().removeprefix("W/") == etag for candidate in if_none_match.split(",")
    )
