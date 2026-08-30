from __future__ import annotations

import argparse

import pytest

from scripts.mysql_backup_restore_check import (
    MysqlBackupRestoreCheckError,
    _quote_identifier,
    _total_rows,
    build_config,
)


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "container": "fitness-mysql",
        "database": "fitness",
        "username": "fitness",
        "password": "secret",
        "timeout_seconds": 180,
        "execute": False,
        "rto_target_seconds": 60.0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_build_config_accepts_local_defaults() -> None:
    config = build_config(_args())

    assert config.container == "fitness-mysql"
    assert config.database == "fitness"
    assert config.execute is False


@pytest.mark.parametrize(
    ("field", "value"),
    [("database", "fitness;DROP"), ("container", "fitness/mysql"), ("username", "fitness user")],
)
def test_build_config_rejects_unsafe_identifiers(field: str, value: str) -> None:
    with pytest.raises(MysqlBackupRestoreCheckError):
        build_config(_args(**{field: value}))


def test_build_config_rejects_invalid_rto() -> None:
    with pytest.raises(MysqlBackupRestoreCheckError):
        build_config(_args(rto_target_seconds=181.0))


def test_identifier_quote_and_row_baseline() -> None:
    assert _quote_identifier("course") == "`course`"
    assert _quote_identifier("a`b") == "`a``b`"
    assert _total_rows({"course": 3, "appointment": 5}) == 8
