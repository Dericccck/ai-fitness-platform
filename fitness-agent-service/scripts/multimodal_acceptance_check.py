"""Opt-in isolated-account smoke test for the deployed mobile multimedia API.

Creates and deletes only its own conversation. Never logs credentials, media URLs,
model output, or user messages. A passing smoke test is not a model quality review.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

API = "/api/v1/mobile/agent"
TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


class AcceptanceError(Exception):
    """A sanitized failure; never include remote body or provider exception."""


def validate_target(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.hostname is not None
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.path in {"", "/"}
            and (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
        )
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise AcceptanceError("服务地址必须是 HTTPS 根地址，或本机 HTTP 根地址")
    return value.rstrip("/")


def valid_id(value: object) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise AcceptanceError("服务返回了无效的资源标识") from None


def request(client: httpx.Client, method: str, path: str, expected: int, **kwargs) -> dict:
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError:
        raise AcceptanceError("网络请求未完成；操作结果可能未知") from None
    if response.status_code != expected:
        raise AcceptanceError(f"服务请求失败（HTTP {response.status_code}）")
    try:
        data = response.json()
    except ValueError:
        raise AcceptanceError("服务响应不是有效 JSON") from None
    if not isinstance(data, dict):
        raise AcceptanceError("服务响应格式无效")
    return data


def run_check(client: httpx.Client, sample: Path, mime: str, mode: str, timeout: float) -> None:
    capabilities = request(client, "GET", API + "/capabilities", 200)
    if mode == "knowledge" and capabilities.get("knowledge_analysis_enabled") is not True:
        raise AcceptanceError("知识素材解读尚未启用")
    conversation = None
    asset = None
    failure = None
    cleanup_failed = False
    try:
        created = request(client, "POST", API + "/conversations", 201)
        conversation = valid_id(created.get("conversation_id"))
        uploaded = request(
            client,
            "POST",
            API + "/media/uploads",
            201,
            json={
                "conversation_id": conversation,
                "idempotency_key": str(uuid4()),
                "content_type": mime,
                "size": sample.stat().st_size,
            },
        )
        asset = valid_id(uploaded.get("asset_id"))
        upload = valid_id(uploaded.get("upload_id"))
        if uploaded.get("upload_mode") != "gateway" or uploaded.get("upload") is not None:
            raise AcceptanceError("验收仅支持服务端中转上传，请检查目标环境配置")
        with sample.open("rb") as stream:
            result = request(
                client,
                "PUT",
                f"{API}/media/uploads/{upload}/content",
                200,
                content=iter(lambda: stream.read(1024 * 1024), b""),
                headers={"Content-Type": mime},
            )
        deadline = time.monotonic() + timeout
        while result.get("status") == "VERIFYING" and time.monotonic() < deadline:
            time.sleep(1)
            result = request(client, "POST", f"{API}/media/uploads/{upload}/complete", 200)
        if result.get("status") != "READY" or result.get("asset_id") != asset:
            raise AcceptanceError("上传未完成或素材标识不匹配")
        run_id = str(uuid4())
        submitted = request(
            client,
            "POST",
            API + "/runs",
            202,
            json={
                "conversation_id": conversation,
                "run_id": run_id,
                "client_message_id": str(uuid4()),
                "message": "请解读画面中的健身知识。"
                if mode == "knowledge"
                else "请分析画面中的训练动作。",
                "media_ids": [asset],
                "analysis_mode": mode,
            },
        )
        if submitted.get("run_id") != run_id:
            raise AcceptanceError("提交的任务标识不匹配")
        while time.monotonic() < deadline:
            result = request(client, "GET", f"{API}/runs/{run_id}", 200)
            if result.get("status") == "COMPLETED":
                break
            if result.get("status") in {"FAILED", "CANCELLED", "UNKNOWN"}:
                code = str(result.get("error_code") or "UNKNOWN")
                code = code if re.fullmatch(r"[A-Z0-9_]{1,80}", code) else "UNKNOWN"
                raise AcceptanceError(f"分析未完成（错误类别 {code}）")
            time.sleep(1)
        else:
            raise AcceptanceError("分析超时，不能认定模型没有计费")
        response = result.get("response") or {}
        report = response.get("report") if isinstance(response, dict) else None
        schema = "fitness-knowledge.v1" if mode == "knowledge" else "movement-analysis.v1"
        if not isinstance(report, dict) or report.get("schema_version") != schema:
            raise AcceptanceError("报告类型不符合预期")
        sources = report.get("source_assets")
        if not isinstance(sources, list) or not any(
            isinstance(item, dict) and item.get("asset_id") == asset for item in sources
        ):
            raise AcceptanceError("报告未绑定本次素材")
        history = request(
            client,
            "GET",
            f"{API}/conversations/{conversation}/messages",
            200,
            params={"run_id": run_id},
        )
        messages = history.get("messages")
        if not isinstance(messages, list) or not any(
            isinstance(item, dict)
            and item.get("role") == "assistant"
            and isinstance(item.get("content"), dict)
            and item["content"].get("schema_version") == schema
            for item in messages
        ):
            raise AcceptanceError("报告历史不可读取")
    except (AcceptanceError, OSError) as exc:
        failure = exc if isinstance(exc, AcceptanceError) else AcceptanceError("测试素材无法读取")
    finally:
        if asset:
            try:
                deletion = request(client, "DELETE", f"{API}/media/{asset}", 202)
                deletion_id = valid_id(deletion.get("deletion_id"))
                result = request(client, "GET", f"{API}/deletions/{deletion_id}", 200)
                # This only confirms the request exists. OSS physical cleanup is asynchronous.
                if result.get("status") not in {"PENDING", "RUNNING", "COMPLETED"}:
                    cleanup_failed = True
            except AcceptanceError:
                cleanup_failed = True
        if conversation:
            try:
                request(client, "DELETE", f"{API}/conversations/{conversation}", 202)
            except AcceptanceError:
                cleanup_failed = True
        if cleanup_failed and conversation:
            print(f"本次测试数据的清理未确认，会话编号：{conversation}", file=sys.stderr)
    if failure:
        raise failure
    if cleanup_failed:
        raise AcceptanceError("测试数据清理未确认")


def main() -> int:
    parser = argparse.ArgumentParser(description="多模态 API 隔离账号验收；会产生模型调用费用")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--sample", required=True, type=Path)
    parser.add_argument("--mode", choices=("movement", "knowledge"), required=True)
    parser.add_argument(
        "--timeout", type=int, default=240, choices=range(10, 601), metavar="10..600"
    )
    parser.add_argument("--execute", action="store_true", help="创建测试数据并调用模型")
    args = parser.parse_args()
    try:
        target = validate_target(args.base_url)
        mime = TYPES.get(args.sample.suffix.lower())
        if (
            not mime
            or not args.sample.is_file()
            or not 0
            < args.sample.stat().st_size
            <= (100 * 1024 * 1024 if mime.startswith("video/") else 10 * 1024 * 1024)
        ):
            raise AcceptanceError("测试素材格式或大小无效")
        if not args.execute:
            print("前置检查通过；未发起请求。使用 --execute 运行，可能产生费用。")
            return 0
        token = os.environ.get("MULTIMODAL_ACCEPTANCE_ACCESS_TOKEN", "")
        if not token or any(ch.isspace() for ch in token):
            raise AcceptanceError("请通过环境变量提供专用测试账号的移动端访问令牌")
        with httpx.Client(
            base_url=target,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30, read=120),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            run_check(client, args.sample, mime, args.mode, float(args.timeout))
        print("链路检查通过；已请求清理测试数据，OSS 物理清理仍需另行核验。")
        return 0
    except AcceptanceError as exc:
        print(f"验收未完成：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
