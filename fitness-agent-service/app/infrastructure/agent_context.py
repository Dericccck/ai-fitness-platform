"""签名 AgentContext 验证和会话隔离工具。

Java Gateway 会在每次 Tool 调用时验证 AgentContext。Agent 服务在会话持久化前再做一次
同密钥验证，只为得到稳定的 subject scope，防止不同用户使用同一个 conversation_id
读取彼此的 LangGraph checkpoint。真正的业务权限仍以 Java Gateway 的验证结果为准。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from app.infrastructure.jwks import JwksPublicKeyProvider


class AgentContextVerificationError(RuntimeError):
    """签名上下文缺失、篡改、过期或 claims 不完整。"""


@dataclass(frozen=True)
class AgentIdentity:
    subject: str
    organization_ids: frozenset[str]
    roles: frozenset[str]
    issued_at: int
    expires_at: int
    # 审核能力和专业资质只能由 Java 认证端写入签名载荷。它们采用可选 claim，
    # 让历史 Token 仍能用于普通对话；但缺失时绝不允许执行专业审核。
    capabilities: frozenset[str] = frozenset()
    qualifications: frozenset[str] = frozenset()


class AgentContextVerifier:
    """与 Java Gateway 保持一致的版本化上下文验证器。

    ``kid`` 让轮换期间的旧 Token 可以使用只读旧密钥验证；当前服务不具备签发能力，
    也不会因为收到未知 kid 而回退到主密钥。缺失 ``alg``/``kid`` 的历史 Token 仍按
    v1 默认值兼容验证，便于本地已有短时上下文平滑升级。RS256 场景只加载公钥，
    私钥不进入 Agent 服务。
    """

    def __init__(
        self,
        secret: str,
        *,
        max_ttl_seconds: int = 300,
        signing_algorithm: str = "HS256",
        signing_key_id: str = "legacy",
        signing_key_ring: dict[str, str] | None = None,
        verification_public_key_ring: dict[str, str] | None = None,
        jwks_provider: JwksPublicKeyProvider | None = None,
    ) -> None:
        self.secret = secret.encode("utf-8")
        self.max_ttl_seconds = max_ttl_seconds
        self.signing_algorithm = signing_algorithm
        self.signing_key_id = signing_key_id
        self.signing_key_ring = {
            key: value.encode("utf-8") for key, value in (signing_key_ring or {}).items()
        }
        self.verification_public_key_ring = dict(verification_public_key_ring or {})
        self.jwks_provider = jwks_provider

    def verify(self, token: str) -> AgentIdentity:
        if not token or len(token) > 8192:
            raise AgentContextVerificationError("invalid agent context")
        parts = token.split(".")
        if len(parts) != 2:
            raise AgentContextVerificationError("invalid agent context format")
        try:
            payload = _decode_base64url(parts[0])
            signature = _decode_base64url(parts[1])
        except ValueError as exc:
            raise AgentContextVerificationError("invalid agent context encoding") from exc

        try:
            claims = json.loads(payload)
            if not isinstance(claims, dict):
                raise TypeError("claims must be an object")
            algorithm = _optional_text(claims, "alg", "HS256")
            key_id = _optional_text(claims, "kid", "legacy")
            if algorithm not in {"HS256", "RS256"} or algorithm != self.signing_algorithm:
                raise ValueError("unsupported signing contract")
            if algorithm == "HS256":
                if not self.signing_key_id:
                    raise ValueError("missing active key id")
                secret = (
                    self.secret
                    if key_id == self.signing_key_id
                    else self.signing_key_ring.get(key_id, b"")
                )
                if not secret:
                    raise ValueError("unknown key id")
                expected = hmac.new(secret, payload, hashlib.sha256).digest()
                if not hmac.compare_digest(expected, signature):
                    raise AgentContextVerificationError("invalid agent context signature")
            else:
                public_key_pem = self.verification_public_key_ring.get(key_id, "")
                public_key: RSAPublicKey | None
                if public_key_pem:
                    public_key = cast(
                        RSAPublicKey,
                        serialization.load_pem_public_key(public_key_pem.encode("utf-8")),
                    )
                elif self.jwks_provider is not None:
                    public_key = self.jwks_provider.get_public_key(key_id)
                else:
                    public_key = None
                if public_key is None:
                    raise ValueError("unknown verification key id")
                try:
                    public_key.verify(
                        signature,
                        payload,
                        padding.PKCS1v15(),
                        hashes.SHA256(),
                    )
                except InvalidSignature as exc:
                    raise AgentContextVerificationError("invalid agent context signature") from exc
            subject = _required_text(claims, "sub")
            organizations = _required_string_set(claims, "orgs")
            roles = _required_string_set(claims, "roles")
            capabilities = _optional_string_set(claims, "capabilities")
            qualifications = _optional_string_set(claims, "qualifications")
            issued_at = _required_int(claims, "iat")
            expires_at = _required_int(claims, "exp")
            _required_text(claims, "nonce")
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise AgentContextVerificationError("invalid agent context claims") from exc

        now = int(time.time())
        if (
            expires_at <= issued_at
            or expires_at > issued_at + self.max_ttl_seconds
            or issued_at > now + 30
            or expires_at <= now
        ):
            raise AgentContextVerificationError("expired or invalid agent context")
        return AgentIdentity(
            subject,
            organizations,
            roles,
            issued_at,
            expires_at,
            capabilities,
            qualifications,
        )


def conversation_thread_id(conversation_id: str, identity: AgentIdentity) -> str:
    """生成不包含原始用户 ID 的稳定 checkpoint thread_id。"""

    scope = (
        f"{identity.subject}:{','.join(sorted(identity.organization_ids))}:"
        f"{','.join(sorted(identity.roles))}:{conversation_id}"
    )
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    return f"fitness:{digest}"


def _decode_base64url(value: str) -> bytes:
    if not value:
        raise ValueError("empty base64 value")
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _required_text(claims: Any, key: str) -> str:
    value = claims[key]
    if not isinstance(value, str) or not value:
        raise ValueError(key)
    return value


def _optional_text(claims: dict[str, Any], key: str, default: str) -> str:
    """读取版本化签名元数据；字段缺失兼容 v1，字段存在则必须是非空字符串。"""

    value = claims.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(key)
    return value


def _required_string_set(claims: Any, key: str) -> frozenset[str]:
    value = claims[key]
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise ValueError(key)
    return frozenset(value)


def _optional_string_set(claims: Any, key: str) -> frozenset[str]:
    """读取签名的可选集合；字段存在但结构异常时仍按篡改处理。"""

    value = claims.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(key)
    return frozenset(value)


def _required_int(claims: Any, key: str) -> int:
    value = claims[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(key)
    return cast(int, value)
