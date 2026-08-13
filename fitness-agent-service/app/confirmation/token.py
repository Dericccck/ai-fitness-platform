"""服务端确认凭证签发器。

当前 Java Gateway 使用的是 HMAC v1 校验协议，因此这里先生成与该协议兼容的短时凭证；
同时把 confirmation_id、tool_id、机构、参数哈希和一次性 JTI 放进载荷，为后续 Gateway
v2 验签升级保留完整绑定字段。Token 只在服务端运行上下文中存在，不通过 HTTP 响应返回。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from app.confirmation.models import ConfirmationRecord


class ConfirmationTokenError(RuntimeError):
    """确认凭证配置或签发失败。"""


@dataclass(frozen=True)
class ConfirmationTokenIssuer:
    """生成绑定批准确认单范围的内部 HMAC 凭证。"""

    secret: str
    ttl_seconds: int = 120

    def __post_init__(self) -> None:
        if len(self.secret.encode("utf-8")) < 32:
            raise ConfirmationTokenError("confirmation signing secret must be at least 32 bytes")
        if self.ttl_seconds < 30 or self.ttl_seconds > 600:
            raise ConfirmationTokenError(
                "confirmation token ttl must be between 30 and 600 seconds"
            )

    def issue(
        self,
        record: ConfirmationRecord,
        *,
        resource: str,
        jti: str,
        now: int | None = None,
    ) -> str:
        """为已经批准且已绑定 JTI 的确认单签发短时 Token。"""

        if record.authorization_status != "APPROVED":
            raise ConfirmationTokenError("only approved confirmation can issue a token")
        if not jti.strip() or not resource.strip():
            raise ConfirmationTokenError("confirmation token scope is incomplete")
        issued_at = int(time.time()) if now is None else now
        expires_at = min(issued_at + self.ttl_seconds, int(record.expires_at.timestamp()))
        if expires_at <= issued_at:
            raise ConfirmationTokenError("confirmation token would be immediately expired")
        payload: dict[str, object] = {
            "sub": record.subject_user_id,
            "action": record.action,
            "resource": resource,
            "request_id": record.request_id,
            "exp": expires_at,
            # 这些字段当前由 Java v1 忽略，但会被 v2 验签和 Agent 审计使用。
            "confirmation_id": record.id,
            "tool_id": record.tool_id,
            "organization_id": record.organization_id,
            "payload_hash": record.payload_hash,
            "jti": jti,
        }
        payload_bytes = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        encoded_payload = _base64url(payload_bytes)
        signature = hmac.new(self.secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
        return f"{encoded_payload}.{_base64url(signature)}"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
