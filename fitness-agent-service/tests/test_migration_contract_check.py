from pathlib import Path

from scripts.migration_contract_check import validate_migrations


def test_repository_migration_chain_is_single_and_bidirectional() -> None:
    migrations_dir = Path(__file__).parents[1] / "migrations" / "versions"

    result = validate_migrations(migrations_dir)

    assert result.passed is True
    assert len(result.revisions) == 35
    assert result.heads == ("20260824_0035",)


def test_migration_contract_rejects_unknown_parent_and_multiple_heads(tmp_path: Path) -> None:
    (tmp_path / "001.py").write_text(
        "revision = '001'\ndown_revision = 'missing'\n\ndef upgrade(): pass\n\ndef downgrade(): pass\n",
        encoding="utf-8",
    )
    (tmp_path / "002.py").write_text(
        "revision = '002'\ndown_revision = None\n\ndef upgrade(): pass\n\ndef downgrade(): pass\n",
        encoding="utf-8",
    )

    result = validate_migrations(tmp_path)

    assert result.passed is False
    assert any("不存在" in error for error in result.errors)
    assert any("只有一个 head" in error for error in result.errors)


def test_migration_contract_requires_downgrade(tmp_path: Path) -> None:
    (tmp_path / "001.py").write_text(
        "revision = '001'\ndown_revision = None\n\ndef upgrade(): pass\n",
        encoding="utf-8",
    )

    result = validate_migrations(tmp_path)

    assert result.passed is False
    assert any("缺少 downgrade()" in error for error in result.errors)
