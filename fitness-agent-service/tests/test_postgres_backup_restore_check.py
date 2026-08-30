import argparse

import pytest

from scripts.postgres_backup_restore_check import (
    PostgresBackupRestoreCheckError,
    _total_rows,
    build_config,
)


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "container": "fitness-agent-postgres",
        "database": "fitness_agent",
        "username": "fitness_agent",
        "password": "secret",
        "timeout_seconds": 180,
        "execute": False,
        "rto_target_seconds": 60.0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_build_config_accepts_rto_target_below_operation_timeout() -> None:
    config = build_config(_args(rto_target_seconds=30.0))

    assert config.rto_target_seconds == 30.0


def test_build_config_rejects_rto_target_above_operation_timeout() -> None:
    with pytest.raises(PostgresBackupRestoreCheckError, match="rto-target-seconds"):
        build_config(_args(timeout_seconds=30, rto_target_seconds=31.0))


def test_total_rows_sums_table_baseline() -> None:
    assert _total_rows({"checkpoints": 3, "knowledge_chunks": 7}) == 10
