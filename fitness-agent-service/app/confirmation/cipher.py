"""确认动作参数的应用层加密边界。

确认单需要在 PostgreSQL 中保存可恢复执行的精确参数，但数据库管理员、备份文件和
Checkpoint 读取者都不应该看到这些参数。这里使用 AES-GCM 提供机密性和完整性；密钥
只从部署 Secret 注入，数据库只保存密文和密钥版本，不保存密钥本身。
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class ConfirmationPayloadCipherError(RuntimeError):
    """确认参数无法安全加密或解密。"""


@dataclass(frozen=True)
class AesGcmPayloadCipher:
    """带密钥版本的 AES-256-GCM 参数加密器。

    密文格式为 ``nonce(12 bytes) + AES-GCM ciphertext``。AAD 不进入密文，但会参与认证；
    调用方使用确认动作的 ``payload_hash`` 作为 AAD，使密文不能被换绑到另一张确认单。
    """

    key: bytes
    key_version: str

    def __post_init__(self) -> None:
        if len(self.key) not in {16, 24, 32}:
            raise ConfirmationPayloadCipherError("AES key must be 128, 192 or 256 bits")
        if not self.key_version.strip():
            raise ConfirmationPayloadCipherError("encryption key version is required")

    @classmethod
    def from_base64(cls, encoded_key: str, key_version: str) -> AesGcmPayloadCipher:
        """从 URL-safe Base64 Secret 构造加密器，不接受弱口令或隐式派生密钥。"""

        if not encoded_key.strip():
            raise ConfirmationPayloadCipherError("confirmation encryption key is not configured")
        try:
            key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise ConfirmationPayloadCipherError(
                "confirmation encryption key is invalid base64"
            ) from exc
        return cls(key=key, key_version=key_version)

    @classmethod
    def generate_for_local_development(cls, key_version: str = "local-v1") -> str:
        """生成本地开发密钥；生产环境必须由 Secret Manager 生成和轮换。"""

        return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")

    def encrypt(self, plaintext: bytes, *, associated_data: str) -> bytes:
        """加密参数并附加随机 nonce；空参数也必须经过认证。"""

        if not associated_data.strip():
            raise ConfirmationPayloadCipherError("associated data is required")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.key).encrypt(nonce, plaintext, associated_data.encode("utf-8"))
        return nonce + ciphertext

    def decrypt(self, ciphertext: bytes, *, associated_data: str) -> bytes:
        """验证 AAD 和 GCM Tag 后解密；任何篡改都转换为稳定错误。"""

        if len(ciphertext) < 12 + 16:
            raise ConfirmationPayloadCipherError("encrypted payload is truncated")
        if not associated_data.strip():
            raise ConfirmationPayloadCipherError("associated data is required")
        try:
            return AESGCM(self.key).decrypt(
                ciphertext[:12], ciphertext[12:], associated_data.encode("utf-8")
            )
        except InvalidTag as exc:
            raise ConfirmationPayloadCipherError("encrypted payload authentication failed") from exc
