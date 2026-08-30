from pathlib import Path

import pytest

from scripts.production_config_contract_check import (
    TEMPLATES,
    ProductionConfigContractError,
    parse_template,
    validate_templates,
)


def test_repository_production_templates_pass() -> None:
    validate_templates()


def test_template_rejects_duplicate_key(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.env"
    path.write_text("A=1\nA=2\n", encoding="utf-8")

    with pytest.raises(ProductionConfigContractError, match="重复"):
        parse_template(path)


def test_template_rejects_local_database_address(tmp_path: Path) -> None:
    path = tmp_path / "local.env"
    source = TEMPLATES["agent"].read_text(encoding="utf-8")
    path.write_text(
        source.replace(
            "AGENT_DATABASE_URL=", "AGENT_DATABASE_URL=jdbc:mysql://127.0.0.1:3307/fitness"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProductionConfigContractError, match="本地地址"):
        validate_templates(
            {
                "agent": path,
            }
        )


def test_template_rejects_wrong_fixed_production_switch(tmp_path: Path) -> None:
    source = TEMPLATES["booking"].read_text(encoding="utf-8")
    path = tmp_path / "booking.env"
    path.write_text(
        source.replace("BOOKING_SCHEMA_INIT_ENABLED=false", "BOOKING_SCHEMA_INIT_ENABLED=true"),
        encoding="utf-8",
    )

    with pytest.raises(ProductionConfigContractError, match="BOOKING_SCHEMA_INIT_ENABLED"):
        validate_templates(
            {
                "booking": path,
            }
        )
