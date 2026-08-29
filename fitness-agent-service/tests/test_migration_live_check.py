import argparse

import pytest

from scripts.migration_live_check import (
    CheckConfig,
    MigrationLiveCheckError,
    build_config,
    build_database_url,
)


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "container": "fitness-agent-postgres",
        "host": "127.0.0.1",
        "port": 5433,
        "admin_database": "postgres",
        "username": "fitness_agent",
        "password": "secret/password",
        "timeout_seconds": 180,
        "execute": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_build_database_url_encodes_credentials() -> None:
    config = CheckConfig(
        container="fitness-agent-postgres",
        host="127.0.0.1",
        port=5433,
        admin_database="postgres",
        username="fitness_agent",
        password="secret/password",
        timeout_seconds=180,
        execute=False,
    )

    assert build_database_url(config, "temporary_db") == (
        "postgresql+asyncpg://fitness_agent:secret%2Fpassword@127.0.0.1:5433/temporary_db"
    )


def test_build_config_rejects_shell_like_database_identifiers() -> None:
    with pytest.raises(MigrationLiveCheckError, match="admin_database"):
        build_config(_args(admin_database="postgres;DROP DATABASE"))


def test_build_config_accepts_read_only_default() -> None:
    config = build_config(_args())

    assert config.execute is False
    assert config.timeout_seconds == 180
