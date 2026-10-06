"""只对昂贵的新对话限流；结果查询、健康检查与确认执行有独立边界。"""

from __future__ import annotations

import hashlib

from fastapi import HTTPException, Request

from app.infrastructure.agent_context import AgentIdentity


async def enforce_chat_quota(request: Request, identity: AgentIdentity) -> None:
    settings = getattr(request.app.state, "settings", None)
    if getattr(request.app.state, "draining", False):
        raise HTTPException(503, "服务正在更新，请稍后再试")
    if settings is None or not settings.chat_rate_limit_enabled:
        return
    scopes = [
        (f"user:{identity.subject}", settings.chat_user_requests_per_minute, 60),
        (f"burst:{identity.subject}", settings.chat_user_burst_per_second, 1),
    ]
    scopes.extend(
        (f"org:{org}", settings.chat_organization_requests_per_minute, 60)
        for org in sorted(identity.organization_ids)
    )
    try:
        for scope, limit, seconds in scopes:
            digest = hashlib.sha256(scope.encode()).hexdigest()
            allowed = await request.app.state.cache.consume_fixed_window(
                f"fitness:agent:chat-quota:{digest}",
                limit=limit,
                window_seconds=seconds,
            )
            if not allowed:
                metrics = getattr(request.app.state, "metrics", None)
                if metrics:
                    metrics.protection_rejections_total.labels(reason="CHAT_QUOTA").inc()
                raise HTTPException(
                    429, "对话请求过于频繁，请稍后再试", headers={"Retry-After": str(seconds)}
                )
    except HTTPException:
        raise
    except Exception as exc:
        metrics = getattr(request.app.state, "metrics", None)
        if metrics:
            metrics.protection_rejections_total.labels(reason="QUOTA_UNAVAILABLE").inc()
        raise HTTPException(503, "对话配额服务暂时不可用") from exc
