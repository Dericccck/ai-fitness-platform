"""校验 Agent 和 Java 健身服务的生产配置模板。

该检查只验证配置契约，不读取 Secret Manager，也不要求真实生产凭证存在。它重点防止
把本地地址、开发密码和本地迁移开关误带入生产模板，避免“模板能提交、服务无法安全启动”。
"""

from __future__ import annotations

import re
from pathlib import Path


class ProductionConfigContractError(ValueError):
    """生产配置模板不符合发布契约。"""


PROJECT_ROOT = Path(__file__).resolve().parents[2]
KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TEMPLATES = {
    "agent": PROJECT_ROOT / "deployment/environments/agent.production.env.example",
    "gateway": PROJECT_ROOT / "deployment/environments/gateway.production.env.example",
    "booking": PROJECT_ROOT / "deployment/environments/booking.production.env.example",
    "training": PROJECT_ROOT / "deployment/environments/training.production.env.example",
    "customer-service": PROJECT_ROOT
    / "deployment/environments/customer-service.production.env.example",
}

REQUIRED_KEYS = {
    "agent": {
        "AGENT_ENVIRONMENT",
        "AGENT_SERVICE_VERSION",
        "AGENT_API_DOCS_ENABLED",
        "AGENT_METRICS_ENABLED",
        "AGENT_OTEL_ENABLED",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_API_KEY",
        "AGENT_DATABASE_URL",
        "AGENT_REDIS_URL",
        "AGENT_GATEWAY_BASE_URL",
        "AGENT_GATEWAY_INTERNAL_SERVICE_TOKEN",
        "AGENT_RAG_STORAGE_BACKEND",
        "AGENT_RAG_S3_ENDPOINT_URL",
        "AGENT_RAG_S3_BUCKET",
        "AGENT_RAG_MALWARE_SCANNER_BACKEND",
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

FIXED_VALUES = {
    ("agent", "AGENT_ENVIRONMENT"): "production",
    ("agent", "AGENT_API_DOCS_ENABLED"): "false",
    ("agent", "AGENT_METRICS_ENABLED"): "true",
    ("agent", "AGENT_OTEL_ENABLED"): "true",
    ("agent", "AGENT_RAG_STORAGE_BACKEND"): "s3",
    ("agent", "AGENT_RAG_MALWARE_SCANNER_BACKEND"): "clamav",
    # 当前项目明确暂不纳入 OCR，避免生产模板引用不存在的推理服务。
    ("agent", "AGENT_RAG_OCR_BACKEND"): "disabled",
    ("gateway", "GATEWAY_CONTEXT_SIGNING_ALGORITHM"): "RS256",
    ("gateway", "GATEWAY_CONFIRMATION_SIGNING_ALGORITHM"): "RS256",
    ("booking", "BOOKING_SCHEMA_INIT_ENABLED"): "false",
    ("booking", "BOOKING_OUTBOX_PUBLISHER_ENABLED"): "true",
    ("training", "TRAINING_SCHEMA_INIT_ENABLED"): "false",
    ("training", "TRAINING_OUTBOX_PUBLISHER_ENABLED"): "true",
    ("customer-service", "CUSTOMER_SERVICE_SCHEMA_INIT_ENABLED"): "false",
}

SENSITIVE_KEYS = {
    "DEEPSEEK_API_KEY",
    "AGENT_DATABASE_URL",
    "AGENT_REDIS_URL",
    "AGENT_GATEWAY_INTERNAL_SERVICE_TOKEN",
    "GATEWAY_DB_PASSWORD",
    "GATEWAY_INTERNAL_SERVICE_TOKEN",
    "GATEWAY_TRAINING_SERVICE_TOKEN",
    "GATEWAY_BOOKING_SERVICE_TOKEN",
    "GATEWAY_CUSTOMER_SERVICE_TOKEN",
    "BOOKING_DB_PASSWORD",
    "BOOKING_RABBITMQ_PASSWORD",
    "BOOKING_INTERNAL_SERVICE_TOKEN",
    "TRAINING_DB_PASSWORD",
    "TRAINING_RABBITMQ_PASSWORD",
    "TRAINING_INTERNAL_SERVICE_TOKEN",
    "CUSTOMER_SERVICE_DB_PASSWORD",
    "CUSTOMER_SERVICE_INTERNAL_SERVICE_TOKEN",
}


def parse_template(path: Path) -> dict[str, str]:
    """解析 env 模板并拒绝重复、非法或无等号配置。"""

    if not path.is_file():
        raise ProductionConfigContractError(f"生产配置模板不存在：{path}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ProductionConfigContractError(f"{path.name}:{line_number} 缺少等号")
        key, value = line.split("=", 1)
        key = key.strip()
        if not KEY_PATTERN.fullmatch(key):
            raise ProductionConfigContractError(f"{path.name}:{line_number} 配置键非法：{key}")
        if key in values:
            raise ProductionConfigContractError(f"{path.name} 存在重复配置键：{key}")
        values[key] = value.strip()
    return values


def validate_templates(templates: dict[str, Path] | None = None) -> None:
    """检查所有生产模板的必填键、固定值和禁止的本地默认配置。"""

    selected_templates = templates or TEMPLATES
    for service, path in selected_templates.items():
        values = parse_template(path)
        missing = sorted(REQUIRED_KEYS[service] - values.keys())
        if missing:
            raise ProductionConfigContractError(f"{service} 缺少生产配置键：{', '.join(missing)}")
        for (fixed_service, key), expected in FIXED_VALUES.items():
            if fixed_service == service and values[key] != expected:
                raise ProductionConfigContractError(
                    f"{service} 的 {key} 必须为 {expected}，实际为 {values[key] or '<空>'}"
                )
        for key, value in values.items():
            lowered = value.lower()
            if any(marker in lowered for marker in ("127.0.0.1", "localhost", ":3307")):
                raise ProductionConfigContractError(
                    f"{service} 的 {key} 仍引用本地地址：请改为生产服务发现地址"
                )
            if any(marker in lowered for marker in ("fitness_dev_2026", "fitness_agent_secret")):
                raise ProductionConfigContractError(f"{service} 的 {key} 包含开发环境凭证")
            if key in SENSITIVE_KEYS and value and "通过 Secret Manager" not in value:
                raise ProductionConfigContractError(
                    f"{service} 的敏感配置 {key} 不应在模板中写入真实值"
                )


def main() -> int:
    """命令行入口，只输出配置契约错误，不输出敏感配置值。"""

    try:
        validate_templates()
    except ProductionConfigContractError as exc:
        print(f"生产配置契约校验失败：{exc}")
        return 1
    print("生产配置契约校验通过：5 个服务模板符合发布边界")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
