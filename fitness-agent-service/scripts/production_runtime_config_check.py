"""校验部署平台实际注入的生产配置。

仓库中的 production env.example 只能证明配置模板没有明显的开发默认值，不能证明
部署平台真的注入了数据库、服务 Token、JWKS 和对象存储配置。本脚本读取部署平台
导出的五份运行时配置文件，但只输出键名和校验结果，绝不输出等号右侧的 Secret。
配置文件应放在仓库外部或 CI 临时目录中，不能提交到 Git。
"""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    # 直接执行脚本时，工作目录是 fitness-agent-service。
    from production_config_contract_check import (
        FIXED_VALUES,
        ProductionConfigContractError,
        parse_template,
    )
except ModuleNotFoundError:
    # pytest 从服务根目录导入 scripts 包时，使用完整模块路径。
    from scripts.production_config_contract_check import (
        FIXED_VALUES,
        ProductionConfigContractError,
        parse_template,
    )


class ProductionRuntimeConfigError(ValueError):
    """部署平台注入的生产配置不满足运行时契约。"""


SERVICE_FILES = {
    "agent": "agent.env",
    "gateway": "gateway.env",
    "booking": "booking.env",
    "training": "training.env",
    "customer-service": "customer-service.env",
}

# 模板中有些兼容配置在 RS256/当前范围下可以为空，例如旧 HMAC 密钥和 OCR 地址。
# 这里单独列出真正启动和运行闭环必需的值，避免把“兼容配置未使用”误判为生产故障。
RUNTIME_REQUIRED_KEYS = {
    "agent": {
        "AGENT_ENVIRONMENT",
        "AGENT_SERVICE_VERSION",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_API_KEY",
        "AGENT_DATABASE_URL",
        "AGENT_CHECKPOINT_DATABASE_URL",
        "AGENT_REDIS_URL",
        "AGENT_CONFIRMATION_ENCRYPTION_KEY_BASE64",
        "AGENT_CONFIRMATION_SIGNING_ALGORITHM",
        "AGENT_CONFIRMATION_SIGNING_KEY_ID",
        "AGENT_CONFIRMATION_SIGNING_PRIVATE_KEY_PEM",
        "AGENT_GATEWAY_BASE_URL",
        "AGENT_GATEWAY_INTERNAL_SERVICE_TOKEN",
        "GATEWAY_CONTEXT_SIGNING_ALGORITHM",
        "GATEWAY_CONTEXT_SIGNING_KEY_ID",
        "GATEWAY_CONTEXT_VERIFICATION_JWKS_URL",
        "AGENT_RAG_STORAGE_BACKEND",
        "AGENT_RAG_S3_ENDPOINT_URL",
        "AGENT_RAG_S3_BUCKET",
        "AGENT_RAG_S3_ACCESS_KEY",
        "AGENT_RAG_S3_SECRET_KEY",
        "AGENT_RAG_MALWARE_SCANNER_BACKEND",
        "AGENT_RAG_CLAMAV_HOST",
        "AGENT_RAG_OCR_BACKEND",
    },
    "gateway": {
        "GATEWAY_SERVER_PORT",
        "GATEWAY_DB_URL",
        "GATEWAY_DB_USERNAME",
        "GATEWAY_DB_PASSWORD",
        "GATEWAY_INTERNAL_SERVICE_TOKEN",
        "GATEWAY_CONTEXT_SIGNING_ALGORITHM",
        "GATEWAY_CONTEXT_VERIFICATION_JWKS_URL",
        "GATEWAY_CONFIRMATION_SIGNING_ALGORITHM",
        "GATEWAY_CONFIRMATION_VERIFICATION_JWKS_URL",
        "GATEWAY_TRAINING_SERVICE_BASE_URL",
        "GATEWAY_TRAINING_SERVICE_TOKEN",
        "GATEWAY_BOOKING_SERVICE_BASE_URL",
        "GATEWAY_BOOKING_SERVICE_TOKEN",
        "GATEWAY_CUSTOMER_SERVICE_BASE_URL",
        "GATEWAY_CUSTOMER_SERVICE_TOKEN",
    },
    "booking": {
        "BOOKING_SERVER_PORT",
        "BOOKING_DB_URL",
        "BOOKING_DB_USERNAME",
        "BOOKING_DB_PASSWORD",
        "BOOKING_RABBITMQ_HOST",
        "BOOKING_RABBITMQ_USERNAME",
        "BOOKING_RABBITMQ_PASSWORD",
        "BOOKING_INTERNAL_SERVICE_TOKEN",
        "BOOKING_SCHEMA_INIT_ENABLED",
        "BOOKING_OUTBOX_PUBLISHER_ENABLED",
    },
    "training": {
        "TRAINING_SERVER_PORT",
        "TRAINING_DB_URL",
        "TRAINING_DB_USERNAME",
        "TRAINING_DB_PASSWORD",
        "TRAINING_RABBITMQ_HOST",
        "TRAINING_RABBITMQ_USERNAME",
        "TRAINING_RABBITMQ_PASSWORD",
        "TRAINING_INTERNAL_SERVICE_TOKEN",
        "TRAINING_SCHEMA_INIT_ENABLED",
        "TRAINING_OUTBOX_PUBLISHER_ENABLED",
    },
    "customer-service": {
        "CUSTOMER_SERVICE_SERVER_PORT",
        "CUSTOMER_SERVICE_DB_URL",
        "CUSTOMER_SERVICE_DB_USERNAME",
        "CUSTOMER_SERVICE_DB_PASSWORD",
        "CUSTOMER_SERVICE_INTERNAL_SERVICE_TOKEN",
        "CUSTOMER_SERVICE_SCHEMA_INIT_ENABLED",
    },
}

_PLACEHOLDER_MARKERS = (
    "<",
    ">",
    "example.com",
    "通过 Secret Manager",
    "changeme",
    "replace-me",
    "your-",
)


def _parse_runtime_file(path: Path) -> dict[str, str]:
    """复用模板解析规则读取运行时文件，不把值写入日志或异常信息。"""

    try:
        return parse_template(path)
    except ProductionConfigContractError as exc:
        raise ProductionRuntimeConfigError(f"运行时配置格式非法：{path.name}") from exc


def _check_value(service: str, key: str, value: str) -> None:
    """校验单个运行时值，错误消息只包含服务和键名。"""

    normalized = value.strip()
    lowered = normalized.lower()
    if not normalized:
        raise ProductionRuntimeConfigError(f"{service} 缺少运行时配置：{key}")
    if any(marker.lower() in lowered for marker in _PLACEHOLDER_MARKERS):
        raise ProductionRuntimeConfigError(f"{service} 仍使用占位配置：{key}")
    if any(marker in lowered for marker in ("127.0.0.1", "localhost", ":3307")):
        raise ProductionRuntimeConfigError(f"{service} 仍引用本地地址：{key}")
    if any(marker in lowered for marker in ("fitness_dev_2026", "fitness_agent_secret")):
        raise ProductionRuntimeConfigError(f"{service} 仍使用开发凭证：{key}")


def validate_runtime_directory(env_dir: Path) -> None:
    """校验五个服务的实际配置和服务间共享 Token 一致性。"""

    if not env_dir.is_dir():
        raise ProductionRuntimeConfigError(f"运行时配置目录不存在：{env_dir}")
    service_values: dict[str, dict[str, str]] = {}
    for service, filename in SERVICE_FILES.items():
        values = _parse_runtime_file(env_dir / filename)
        missing = sorted(RUNTIME_REQUIRED_KEYS[service] - values.keys())
        if missing:
            raise ProductionRuntimeConfigError(
                f"{service} 缺少运行时配置键：{', '.join(missing)}"
            )
        for key in RUNTIME_REQUIRED_KEYS[service]:
            _check_value(service, key, values[key])
        for (fixed_service, key), expected in FIXED_VALUES.items():
            if fixed_service == service and values.get(key) != expected:
                raise ProductionRuntimeConfigError(f"{service} 的固定开关不符合生产契约：{key}")
        service_values[service] = values

    agent = service_values["agent"]
    gateway = service_values["gateway"]
    booking = service_values["booking"]
    training = service_values["training"]
    customer_service = service_values["customer-service"]
    pairs = (
        (agent, "AGENT_GATEWAY_INTERNAL_SERVICE_TOKEN", gateway, "GATEWAY_INTERNAL_SERVICE_TOKEN"),
        (gateway, "GATEWAY_TRAINING_SERVICE_TOKEN", training, "TRAINING_INTERNAL_SERVICE_TOKEN"),
        (gateway, "GATEWAY_BOOKING_SERVICE_TOKEN", booking, "BOOKING_INTERNAL_SERVICE_TOKEN"),
        (gateway, "GATEWAY_CUSTOMER_SERVICE_TOKEN", customer_service, "CUSTOMER_SERVICE_INTERNAL_SERVICE_TOKEN"),
    )
    for left, left_key, right, right_key in pairs:
        if left[left_key] != right[right_key]:
            raise ProductionRuntimeConfigError(
                f"跨服务 Token 不一致：{left_key} 与 {right_key}"
            )

    if agent["AGENT_RAG_OCR_BACKEND"] != "disabled":
        raise ProductionRuntimeConfigError("当前项目范围要求 AGENT_RAG_OCR_BACKEND=disabled")


def build_parser() -> argparse.ArgumentParser:
    """构造运行时配置门禁命令。"""

    parser = argparse.ArgumentParser(description="校验实际注入的生产运行时配置")
    parser.add_argument(
        "--env-dir",
        type=Path,
        required=True,
        help="仓库外部的运行时配置目录，需包含 agent.env 等五个文件",
    )
    return parser


def main() -> int:
    """命令行入口；失败时不输出 Secret 值。"""

    try:
        args = build_parser().parse_args()
        validate_runtime_directory(args.env_dir)
    except ProductionRuntimeConfigError as exc:
        print(f"生产运行时配置校验失败：{exc}")
        return 1
    print("生产运行时配置校验通过：5 个服务配置完整，固定开关和共享 Token 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
