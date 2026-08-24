"""认证服务 JWKS 公钥读取与缓存。

该模块只接收 RSA 公钥，不接收私钥。缓存过期后刷新失败会抛出异常，让上层拒绝当前
RS256 请求；不会为了可用性继续使用已经过期的公钥缓存。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey, RSAPublicNumbers


class JwksUnavailableError(RuntimeError):
    """JWKS 地址不可用、返回格式不合法或没有可用公钥。"""


@dataclass(frozen=True)
class _JwksSnapshot:
    keys: dict[str, RSAPublicKey]
    expires_at: float


class JwksPublicKeyProvider:
    """按 kid 获取 JWKS RSA 公钥，并在有效期内复用快照。"""

    _UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS = 30

    def __init__(
        self,
        url: str,
        *,
        cache_ttl_seconds: int = 300,
        timeout_seconds: float = 2.0,
    ) -> None:
        self.url = url.strip()
        self.cache_ttl_seconds = cache_ttl_seconds
        self.timeout_seconds = timeout_seconds
        self._snapshot: _JwksSnapshot | None = None
        self._lock = threading.Lock()
        self._last_unknown_kid_refresh_at: float | None = None

    def get_public_key(self, key_id: str) -> RSAPublicKey | None:
        """返回公钥；JWKS 未配置时返回 None，配置但刷新失败时 fail-closed。"""

        if not self.url:
            return None
        now = time.monotonic()
        snapshot = self._snapshot
        refreshed = False
        if snapshot is None or now >= snapshot.expires_at:
            with self._lock:
                snapshot = self._snapshot
                if snapshot is None or now >= snapshot.expires_at:
                    snapshot = self._refresh(now)
                    self._snapshot = snapshot
                    refreshed = True
        public_key = snapshot.keys.get(key_id)
        if public_key is not None or refreshed:
            return public_key

        # 密钥轮换时新 kid 可能早于缓存 TTL 出现。只对当前缓存没有的 kid 触发一次
        # 受控刷新，并设置冷却窗口，避免攻击者伪造大量 kid 让 Agent 反复请求认证服务。
        with self._lock:
            snapshot = self._snapshot
            if snapshot is None:
                return None
            public_key = snapshot.keys.get(key_id)
            if public_key is not None:
                return public_key
            last_refresh = self._last_unknown_kid_refresh_at
            if (
                last_refresh is not None
                and now < last_refresh + self._UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS
            ):
                return None
            self._last_unknown_kid_refresh_at = now
            snapshot = self._refresh(now)
            self._snapshot = snapshot
            return snapshot.keys.get(key_id)

    def _refresh(self, now: float) -> _JwksSnapshot:
        try:
            response = httpx.get(self.url, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            keys = _parse_jwks(payload)
        except JwksUnavailableError:
            raise
        except Exception as exc:
            raise JwksUnavailableError("agent context JWKS is unavailable") from exc
        return _JwksSnapshot(keys=keys, expires_at=now + self.cache_ttl_seconds)


def _parse_jwks(payload: Any) -> dict[str, RSAPublicKey]:
    if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
        raise JwksUnavailableError("invalid agent context JWKS")
    if len(payload["keys"]) > 50:
        raise JwksUnavailableError("agent context JWKS contains too many keys")

    result: dict[str, RSAPublicKey] = {}
    for item in payload["keys"]:
        if not isinstance(item, dict):
            raise JwksUnavailableError("invalid agent context JWKS key")
        key_id = _required_text(item, "kid")
        if item.get("kty") != "RSA" or item.get("alg", "RS256") != "RS256":
            raise JwksUnavailableError("invalid agent context JWKS key algorithm")
        if item.get("use", "sig") != "sig":
            raise JwksUnavailableError("invalid agent context JWKS key use")
        modulus = int.from_bytes(_decode_base64url(_required_text(item, "n")), "big")
        exponent = int.from_bytes(_decode_base64url(_required_text(item, "e")), "big")
        if modulus <= 0 or exponent <= 0:
            raise JwksUnavailableError("invalid agent context RSA key")
        result[key_id] = RSAPublicNumbers(exponent, modulus).public_key()
    return result


def _required_text(item: dict[str, Any], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise JwksUnavailableError(f"invalid agent context JWKS field: {field}")
    return value


def _decode_base64url(value: str) -> bytes:
    import base64

    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError as exc:
        raise JwksUnavailableError("invalid agent context JWKS encoding") from exc
