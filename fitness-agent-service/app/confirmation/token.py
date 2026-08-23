"""服务端确认凭证签发器。

当前默认使用 HMAC v1，但已经校验完整的 confirmation_id、tool_id、机构、参数哈希和一次性
JTI 绑定字段；配置 RS256 后由 Agent 使用私钥签发，Gateway 只使用公钥验证。Token 只在
服务端运行上下文中存在，不通过 HTTP 响应返回。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.confirmation.models import ConfirmationRecord


class ConfirmationTokenError(RuntimeError):
    """确认凭证配置或签发失败。"""


@dataclass(frozen=True)
class ConfirmationTokenIssuer:
    """生成绑定批准确认单范围的内部 HMAC 或 RS256 凭证。"""

    secret: str
    ttl_seconds: int = 120
    signing_algorithm: str = "HS256"
    signing_key_id: str = "legacy"
    signing_private_key_pem: str = ""

    def __post_init__(self) -> None:
        if self.signing_algorithm not in {"HS256", "RS256"}:
            raise ConfirmationTokenError("confirmation signing algorithm must be HS256 or RS256")
        if not self.signing_key_id.strip():
            raise ConfirmationTokenError("confirmation signing key id must not be empty")
        if self.signing_algorithm == "HS256" and len(self.secret.encode("utf-8")) < 32:
            raise ConfirmationTokenError("confirmation signing secret must be at least 32 bytes")
        if self.signing_algorithm == "RS256" and not self.signing_private_key_pem.strip():
            raise ConfirmationTokenError("confirmation RSA private key must be configured")
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
            "alg": self.signing_algorithm,
            "kid": self.signing_key_id,
            "sub": record.subject_user_id,
            "action": record.action,
            "resource": resource,
            "request_id": record.request_id,
            "exp": expires_at,
            # Gateway 会校验这些字段并转发脱离原始 Token 的声明；JTI 最终在训练服务事务中消费。
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
        if self.signing_algorithm == "HS256":
            signature = hmac.new(self.secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
        else:
            private_key = serialization.load_pem_private_key(
                self.signing_private_key_pem.encode("utf-8"), password=None
            )
            signature = private_key.sign(payload_bytes, padding.PKCS1v15(), hashes.SHA256())
        return f"{encoded_payload}.{_base64url(signature)}"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
