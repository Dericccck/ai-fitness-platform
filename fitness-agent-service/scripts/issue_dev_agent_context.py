#!/usr/bin/env python3
"""签发仅限本地开发使用的短时 AgentContext。

这个脚本不是认证服务，也不能部署成生产接口。它只用于在本地已经启动
Agent 和 Java Gateway 后，给 Operations 真实冒烟联调提供一个可验证的
受控角色上下文。

安全边界：
1. 必须显式设置 ``FITNESS_DEV_CONTEXT_ISSUER=1`` 才允许运行；
2. 角色只允许从组织管理员、教练、学员白名单中选择，不能签发系统管理员；
3. 有效期最多 5 分钟；
4. 共享签名密钥只从环境变量或本地 ``.env`` 读取，永远不打印；
5. 标准输出只包含 Token，方便直接赋值给 ``AGENT_LIVE_AGENT_CONTEXT``。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from collections.abc import Mapping
from pathlib import Path

MAX_CONTEXT_TTL_SECONDS = 300
DEFAULT_SUBJECT = "local-operations-admin"
DEFAULT_ROLE = "ORGANIZATION_ADMIN"
ALLOWED_DEV_ROLES = frozenset({"ORGANIZATION_ADMIN", "COACH", "STUDENT"})
DEV_FLAG = "FITNESS_DEV_CONTEXT_ISSUER"
SECRET_ENV = "GATEWAY_CONTEXT_SIGNING_SECRET"
ALGORITHM_ENV = "GATEWAY_CONTEXT_SIGNING_ALGORITHM"
KEY_ID_ENV = "GATEWAY_CONTEXT_SIGNING_KEY_ID"
SUPPORTED_ALGORITHM = "HS256"
DEFAULT_KEY_ID = "legacy"


class DevContextIssuerError(RuntimeError):
    """本地开发上下文签发前置条件不满足。"""


def _decode_dotenv_value(raw_value: str) -> str:
    """读取最小 `.env` 语法，避免为了本地工具引入额外运行时依赖。"""

    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def load_local_env(env_path: Path) -> dict[str, str]:
    """读取本地 `.env`，但不覆盖调用终端已经显式设置的变量。"""

    values: dict[str, str] = {}
    if not env_path.is_file():
        return values
    for line_number, line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise DevContextIssuerError(f".env 第 {line_number} 行缺少 '='")
        name, raw_value = stripped.split("=", 1)
        name = name.strip()
        if not name or not name.replace("_", "").isalnum():
            raise DevContextIssuerError(f".env 第 {line_number} 行变量名非法")
        values[name] = _decode_dotenv_value(raw_value)
    return values


def _get_setting(name: str, dotenv_values: Mapping[str, str]) -> str:
    """优先使用终端环境变量，再回退到本地 `.env`。"""

    return os.getenv(name, dotenv_values.get(name, "")).strip()


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def issue_token(
    *,
    secret: str,
    subject: str,
    organization_id: str,
    role: str = DEFAULT_ROLE,
    ttl_seconds: int = MAX_CONTEXT_TTL_SECONDS,
    signing_algorithm: str = SUPPORTED_ALGORITHM,
    key_id: str = DEFAULT_KEY_ID,
    now: int | None = None,
) -> str:
    """按照 Java Gateway v1 契约生成 `payload.signature` Token。"""

    if not secret:
        raise DevContextIssuerError(f"缺少 {SECRET_ENV}")
    if signing_algorithm != SUPPORTED_ALGORITHM:
        raise DevContextIssuerError(f"本地签发器只支持 {SUPPORTED_ALGORITHM}")
    if not key_id.strip():
        raise DevContextIssuerError("本地测试 key_id 不能为空")
    if not subject.strip():
        raise DevContextIssuerError("本地测试 subject 不能为空")
    if not organization_id.strip():
        raise DevContextIssuerError("本地测试 organization_id 不能为空")
    normalized_role = role.strip().upper()
    if normalized_role not in ALLOWED_DEV_ROLES:
        allowed = ", ".join(sorted(ALLOWED_DEV_ROLES))
        raise DevContextIssuerError(f"本地测试 role 只允许：{allowed}")
    if not 1 <= ttl_seconds <= MAX_CONTEXT_TTL_SECONDS:
        raise DevContextIssuerError(f"Token 有效期必须在 1 到 {MAX_CONTEXT_TTL_SECONDS} 秒之间")

    issued_at = int(time.time()) if now is None else now
    claims = {
        "alg": signing_algorithm,
        "kid": key_id.strip(),
        "sub": subject.strip(),
        "orgs": [organization_id.strip()],
        "roles": [normalized_role],
        "capabilities": [],
        "qualifications": [],
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
        "nonce": secrets.token_urlsafe(16),
    }
    payload = json.dumps(claims, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_encode_base64url(payload)}.{_encode_base64url(signature)}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="签发本地开发用受控角色 AgentContext")
    parser.add_argument("--subject", default=DEFAULT_SUBJECT, help="本地测试主体 ID")
    parser.add_argument("--org-id", help="机构 ID；也可以通过 DEV_AGENT_ORG_ID 提供")
    parser.add_argument(
        "--role",
        default=DEFAULT_ROLE,
        choices=sorted(ALLOWED_DEV_ROLES),
        help="本地测试角色；只允许组织管理员、教练或学员",
    )
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=MAX_CONTEXT_TTL_SECONDS,
        help=f"有效期，最多 {MAX_CONTEXT_TTL_SECONDS} 秒",
    )
    return parser.parse_args()


def main() -> int:
    """校验本地开关并只向标准输出写入 Token。"""

    if os.getenv(DEV_FLAG) != "1":
        print(
            f"拒绝签发：必须显式设置 {DEV_FLAG}=1。该脚本仅限本地开发使用。",
            file=sys.stderr,
        )
        return 2

    args = _parse_args()
    dotenv_values = load_local_env(Path(__file__).resolve().parents[1] / ".env")
    secret = _get_setting(SECRET_ENV, dotenv_values)
    signing_algorithm = _get_setting(ALGORITHM_ENV, dotenv_values) or SUPPORTED_ALGORITHM
    key_id = _get_setting(KEY_ID_ENV, dotenv_values) or DEFAULT_KEY_ID
    organization_id = (args.org_id or os.getenv("DEV_AGENT_ORG_ID", "")).strip()
    try:
        token = issue_token(
            secret=secret,
            subject=args.subject,
            organization_id=organization_id,
            role=args.role,
            ttl_seconds=args.ttl_seconds,
            signing_algorithm=signing_algorithm,
            key_id=key_id,
        )
    except DevContextIssuerError as exc:
        print(f"无法签发本地 AgentContext：{exc}", file=sys.stderr)
        return 2

    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
