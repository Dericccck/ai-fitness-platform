"""验证真实认证服务 JWKS 的最小运行契约。

该脚本只读取 JWKS，不签发 Token、不修改数据库，也不会输出公钥正文。它用于部署前
确认 URL 可访问、文档格式正确，并且认证服务已经发布了预期 ``kid`` 的 RSA 签名公钥。
"""

from __future__ import annotations

import argparse
import os
import sys

from app.infrastructure.jwks import JwksPublicKeyProvider, JwksUnavailableError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="验证认证服务 JWKS URL 和指定 kid")
    parser.add_argument(
        "--jwks-url",
        default=os.getenv("JWKS_URL", ""),
        help="标准 JWKS 地址，也可通过 JWKS_URL 提供",
    )
    parser.add_argument(
        "--key-id",
        default=os.getenv("JWKS_KID", ""),
        help="预期存在的 kid，也可通过 JWKS_KID 提供",
    )
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    return parser


def run_check(jwks_url: str, key_id: str, timeout_seconds: float) -> int:
    if not jwks_url.strip() or not key_id.strip():
        print("[失败] 必须提供 JWKS_URL/JWKS_KID，或传入 --jwks-url/--key-id", file=sys.stderr)
        return 2
    provider = JwksPublicKeyProvider(jwks_url, timeout_seconds=timeout_seconds)
    try:
        public_key = provider.get_public_key(key_id)
    except JwksUnavailableError as exc:
        print(f"[失败] JWKS 不可用或格式不合法：{exc}", file=sys.stderr)
        return 1
    if public_key is None:
        print(f"[失败] JWKS 中没有预期 kid：{key_id}", file=sys.stderr)
        return 1
    print(f"[通过] JWKS 可访问，已找到 RSA 签名公钥 kid={key_id}")
    return 0


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    raise SystemExit(run_check(arguments.jwks_url, arguments.key_id, arguments.timeout_seconds))
