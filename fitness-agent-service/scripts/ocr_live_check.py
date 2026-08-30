"""执行独立 OCR 服务的真实联调检查。

该脚本不写入知识库，也不把 OCR 正文输出到终端。它验证 OCR 服务进程、模型就绪状态、
Bearer 鉴权链路（如果配置了 Token）和 `ocr-service-v1` 响应结构，方便 Linux/GPU
环境准备好后直接对真实 PDF 做受控验收。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

EXPECTED_CONTRACT_VERSION = "ocr-service-v1"


class OcrLiveCheckError(RuntimeError):
    """OCR 真实联调没有达到预期。"""


def build_parser() -> argparse.ArgumentParser:
    """构造 OCR 联调参数；默认优先读取环境变量，避免把 Token 写进命令历史。"""

    parser = argparse.ArgumentParser(description="独立 OCR 服务真实联调检查")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("OCR_LIVE_ENDPOINT", os.getenv("KNOWLEDGE_OCR_ENDPOINT", "")),
        help="OCR /v1/parse 地址，默认读取 OCR_LIVE_ENDPOINT 或 KNOWLEDGE_OCR_ENDPOINT",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("OCR_LIVE_API_KEY", os.getenv("KNOWLEDGE_OCR_API_KEY", "")),
        help="可选 OCR Bearer Token，默认读取环境变量，不会打印",
    )
    parser.add_argument(
        "--sample-pdf",
        default=os.getenv("OCR_LIVE_SAMPLE_PDF", ""),
        help="用于真实解析契约检查的 PDF 路径，默认读取 OCR_LIVE_SAMPLE_PDF",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("OCR_LIVE_TIMEOUT_SECONDS", "300")),
        help="健康检查和解析请求的超时时间，默认 300 秒",
    )
    return parser


def _service_root(endpoint: str) -> str:
    """从 /v1/parse 地址得到同一服务的健康检查根地址。"""

    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise OcrLiveCheckError("--endpoint 必须是完整 HTTP(S) URL")
    path = parsed.path.rstrip("/")
    path = path.removesuffix("/v1/parse")
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", "")).rstrip("/")


def validate_health_response(name: str, status_code: int, payload: Any) -> str:
    """校验健康探针，只返回脱敏摘要，不暴露服务响应正文。"""

    if status_code >= 400 or not isinstance(payload, dict):
        raise OcrLiveCheckError(f"{name} 探针失败：HTTP {status_code} 或响应不是 JSON 对象")
    expected = "UP" if name == "live" else "READY"
    if payload.get("status") != expected:
        raise OcrLiveCheckError(f"{name} 探针状态异常")
    if name == "ready" and not isinstance(payload.get("engine"), str):
        raise OcrLiveCheckError("ready 探针缺少引擎名称")
    return "进程可响应" if name == "live" else "模型已就绪"


def validate_parse_response(payload: Any) -> tuple[int, float, float]:
    """校验 OCR 结果的最小可发布证据，不返回正文，避免泄露文档内容。"""

    if not isinstance(payload, dict):
        raise OcrLiveCheckError("OCR 解析响应不是 JSON 对象")
    if payload.get("contract_version") != EXPECTED_CONTRACT_VERSION:
        raise OcrLiveCheckError("OCR 响应契约版本不受支持")
    if payload.get("media_type") != "application/pdf":
        raise OcrLiveCheckError("OCR 响应 media_type 异常")
    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        raise OcrLiveCheckError("OCR warnings 不是字符串数组")
    blocks = payload.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise OcrLiveCheckError("OCR 响应没有可索引 blocks")

    confidences: list[float] = []
    for block in blocks:
        if not isinstance(block, dict):
            raise OcrLiveCheckError("OCR block 不是对象")
        if block.get("kind") not in {"TEXT", "TABLE"}:
            raise OcrLiveCheckError("OCR block kind 异常")
        if not isinstance(block.get("content"), str) or not block["content"].strip():
            raise OcrLiveCheckError("OCR block content 为空")
        page = block.get("source_page")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise OcrLiveCheckError("OCR block source_page 异常")
        confidence = block.get("confidence")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= confidence <= 1
        ):
            raise OcrLiveCheckError("OCR block confidence 异常")
        region = block.get("source_region")
        if not isinstance(region, dict):
            raise OcrLiveCheckError("OCR block 缺少 source_region")
        values = {key: region.get(key) for key in ("x", "y", "width", "height")}
        if any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in values.values()
        ):
            raise OcrLiveCheckError("OCR source_region 坐标异常")
        x, y, width, height = (float(values[key]) for key in ("x", "y", "width", "height"))
        if (
            not 0 <= x <= 1
            or not 0 <= y <= 1
            or not 0 < width <= 1
            or not 0 < height <= 1
            or x + width > 1
            or y + height > 1
        ):
            raise OcrLiveCheckError("OCR source_region 越出页面范围")
        confidences.append(float(confidence))
    return len(blocks), min(confidences), max(confidences)


def _run_live_check(args: argparse.Namespace, client: httpx.Client) -> None:
    """使用已创建的 HTTP 客户端执行检查，便于测试完整 HTTP 编排。"""

    root = _service_root(args.endpoint)
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    try:
        for name in ("live", "ready"):
            response = client.get(f"{root}/health/{name}")
            payload = response.json()
            detail = validate_health_response(name, response.status_code, payload)
            print(f"[通过] health-{name}: {detail}")
        sample_path = Path(args.sample_pdf)
        with sample_path.open("rb") as stream:
            response = client.post(
                args.endpoint,
                headers=headers,
                files={"file": (sample_path.name, stream, "application/pdf")},
            )
        if response.status_code >= 400:
            raise OcrLiveCheckError(f"parse 请求失败：HTTP {response.status_code}")
        blocks, minimum, maximum = validate_parse_response(response.json())
    except OcrLiveCheckError:
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise OcrLiveCheckError("OCR 服务联调请求失败") from exc
    print(f"[通过] parse-contract: blocks={blocks}, confidence_range={minimum:.4f}-{maximum:.4f}")


def run_live_check(
    args: argparse.Namespace,
    client: httpx.Client | None = None,
) -> None:
    """执行健康检查和一次真实 PDF 解析契约检查。

    正式命令使用函数内部创建的客户端；测试可以注入 MockTransport，验证完整 HTTP
    编排、鉴权请求头和 multipart 文件上传，而不会访问真实 OCR 服务。
    """

    if args.timeout_seconds <= 0:
        raise OcrLiveCheckError("--timeout-seconds 必须大于 0")
    if not args.endpoint:
        raise OcrLiveCheckError("缺少 --endpoint 或 OCR_LIVE_ENDPOINT")
    if not args.sample_pdf:
        raise OcrLiveCheckError("缺少 --sample-pdf 或 OCR_LIVE_SAMPLE_PDF")
    sample_path = Path(args.sample_pdf)
    if not sample_path.is_file():
        raise OcrLiveCheckError("sample PDF 不存在或不是文件")
    if client is not None:
        _run_live_check(args, client)
        return
    with httpx.Client(timeout=args.timeout_seconds) as owned_client:
        _run_live_check(args, owned_client)


def main() -> int:
    """命令行入口，失败时只输出安全诊断。"""

    try:
        run_live_check(build_parser().parse_args())
    except OcrLiveCheckError as exc:
        print(f"[失败] ocr-live-check: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
