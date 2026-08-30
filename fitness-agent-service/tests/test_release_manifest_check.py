import pytest

from scripts.release_manifest_check import (
    ReleaseManifestError,
    build_manifest,
    config_contract_checksum,
    read_config_keys,
    validate_manifest,
)


def _keys() -> tuple[str, ...]:
    return ("AGENT_ENVIRONMENT", "AGENT_SERVICE_VERSION", "DEEPSEEK_API_KEY")


def _manifest() -> dict[str, object]:
    commit = "a" * 40
    return build_manifest(
        environment="staging",
        source_commit=commit,
        image=f"registry.example.com/fitness-agent@sha256:{'b' * 64}",
        config_keys=_keys(),
    )


def test_build_manifest_binds_production_version_to_commit() -> None:
    manifest = _manifest()

    assert manifest["service"] == "fitness-agent-service"
    assert manifest["service_version"] == "a" * 40
    assert manifest["required_config"] == list(_keys())
    assert manifest["config_contract_sha256"] == config_contract_checksum(_keys())


def test_validate_manifest_rejects_mutable_image_tag() -> None:
    manifest = _manifest()
    manifest["image"] = "registry.example.com/fitness-agent:latest"

    with pytest.raises(ReleaseManifestError, match="sha256 digest"):
        validate_manifest(manifest, expected_config_keys=_keys())


def test_validate_manifest_rejects_version_not_bound_to_commit() -> None:
    manifest = _manifest()
    manifest["service_version"] = "release-2026.08"

    with pytest.raises(ReleaseManifestError, match="service_version"):
        validate_manifest(manifest, expected_config_keys=_keys())


def test_validate_manifest_rejects_config_contract_drift() -> None:
    manifest = _manifest()
    manifest["required_config"] = ["AGENT_ENVIRONMENT"]

    with pytest.raises(ReleaseManifestError, match="required_config"):
        validate_manifest(manifest, expected_config_keys=_keys())


def test_read_config_keys_ignores_comments_and_values(tmp_path) -> None:
    config_file = tmp_path / "agent.env.example"
    config_file.write_text(
        "# Secret must never be read\nAGENT_B=hidden\n\nAGENT_A=value\n",
        encoding="utf-8",
    )

    assert read_config_keys(config_file) == ("AGENT_A", "AGENT_B")


def test_read_config_keys_rejects_duplicate_keys(tmp_path) -> None:
    config_file = tmp_path / "agent.env.example"
    config_file.write_text("AGENT_A=one\nAGENT_A=two\n", encoding="utf-8")

    with pytest.raises(ReleaseManifestError, match="重复键名"):
        read_config_keys(config_file)
