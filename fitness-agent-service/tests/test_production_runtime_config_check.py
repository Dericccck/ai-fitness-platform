from __future__ import annotations

from pathlib import Path

import pytest

from scripts.production_config_contract_check import FIXED_VALUES, REQUIRED_KEYS
from scripts.production_runtime_config_check import (
    RUNTIME_REQUIRED_KEYS,
    SERVICE_FILES,
    ProductionRuntimeConfigError,
    validate_runtime_directory,
)


def _write_runtime_files(directory: Path) -> None:
    values_by_service: dict[str, dict[str, str]] = {}
    for service in SERVICE_FILES:
        values = {
            key: "runtime-value" for key in REQUIRED_KEYS[service] | RUNTIME_REQUIRED_KEYS[service]
        }
        values.update(
            {
                key: expected
                for (fixed_service, key), expected in FIXED_VALUES.items()
                if fixed_service == service
            }
        )
        values_by_service[service] = values

    # 测试值使用稳定的非本地地址，且共享 Token 明确保持一致。
    values_by_service["agent"].update(
        {
            "AGENT_DATABASE_URL": "postgresql+asyncpg://agent@postgres.internal/agent",
            "AGENT_CHECKPOINT_DATABASE_URL": "postgresql://agent@postgres.internal/agent",
            "AGENT_REDIS_URL": "redis://redis.internal:6379/0",
            "AGENT_GATEWAY_BASE_URL": "http://gateway.internal:8081",
            "GATEWAY_CONTEXT_VERIFICATION_JWKS_URL": "https://auth.internal/.well-known/jwks.json",
            "AGENT_RAG_S3_ENDPOINT_URL": "https://object.internal",
            "AGENT_GATEWAY_INTERNAL_SERVICE_TOKEN": "shared-gateway-token",
            "AGENT_RAG_OCR_BACKEND": "disabled",
        }
    )
    values_by_service["gateway"].update(
        {
            "GATEWAY_DB_URL": "jdbc:mysql://mysql.internal:3306/fitness",
            "GATEWAY_CONTEXT_VERIFICATION_JWKS_URL": "https://auth.internal/.well-known/jwks.json",
            "GATEWAY_CONFIRMATION_VERIFICATION_JWKS_URL": "https://auth.internal/.well-known/confirmation-jwks.json",
            "GATEWAY_INTERNAL_SERVICE_TOKEN": "shared-gateway-token",
            "GATEWAY_TRAINING_SERVICE_TOKEN": "shared-training-token",
            "GATEWAY_BOOKING_SERVICE_TOKEN": "shared-booking-token",
            "GATEWAY_CUSTOMER_SERVICE_TOKEN": "shared-customer-token",
        }
    )
    for service, token_key, token in (
        ("booking", "BOOKING_INTERNAL_SERVICE_TOKEN", "shared-booking-token"),
        ("training", "TRAINING_INTERNAL_SERVICE_TOKEN", "shared-training-token"),
        ("customer-service", "CUSTOMER_SERVICE_INTERNAL_SERVICE_TOKEN", "shared-customer-token"),
    ):
        values_by_service[service][token_key] = token
        db_key = next(key for key in values_by_service[service] if key.endswith("_DB_URL"))
        values_by_service[service][db_key] = "jdbc:mysql://mysql.internal:3306/fitness"

    directory.mkdir()
    for service, filename in SERVICE_FILES.items():
        content = "\n".join(
            f"{key}={value}" for key, value in sorted(values_by_service[service].items())
        )
        (directory / filename).write_text(content + "\n", encoding="utf-8")


def test_runtime_config_accepts_complete_values(tmp_path: Path) -> None:
    _write_runtime_files(tmp_path / "runtime")

    validate_runtime_directory(tmp_path / "runtime")


def test_runtime_config_rejects_missing_secret(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    _write_runtime_files(runtime_dir)
    gateway = runtime_dir / "gateway.env"
    gateway.write_text(
        gateway.read_text(encoding="utf-8").replace(
            "GATEWAY_DB_PASSWORD=runtime-value", "GATEWAY_DB_PASSWORD="
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProductionRuntimeConfigError, match="GATEWAY_DB_PASSWORD"):
        validate_runtime_directory(runtime_dir)


def test_runtime_config_rejects_mismatched_service_token(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    _write_runtime_files(runtime_dir)
    booking = runtime_dir / "booking.env"
    booking.write_text(
        booking.read_text(encoding="utf-8").replace(
            "BOOKING_INTERNAL_SERVICE_TOKEN=shared-booking-token",
            "BOOKING_INTERNAL_SERVICE_TOKEN=another-token",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProductionRuntimeConfigError, match="跨服务 Token"):
        validate_runtime_directory(runtime_dir)
