import json

import pytest

from scripts.release_manifest_check import (
    ReleaseManifestError,
    build_manifest,
    build_manifest_v2,
    config_contract_checksum,
    main,
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


def test_build_and_validate_manifest_v2_tracks_release_components() -> None:
    commit = "c" * 40
    digest = "sha256:" + "d" * 64
    manifest = build_manifest_v2(
        environment="staging",
        source_commit=commit,
        image=f"registry.example.com/fitness-agent@{digest}",
        config_keys=_keys(),
        prompts={"supervisor": "route by domain", "fitness": "answer safely"},
        safe_config={"agent_max_tool_steps": 4, "rag_prompt_max_evidence_chars": 1800},
        model_artifacts={"embedding": {"digest": digest, "dimension": 1536}},
        tool_schemas={"fitness.user.get_current.v1": {"version": 1, "fields": ["id"]}},
        eval_release_id="eval-20260905-v1",
        eval_dataset_digest=digest,
        eval_thresholds_digest=digest,
        index_build_id="kb-build-17",
    )

    assert manifest["schema_version"] == 2
    assert manifest["components"]["evaluation"]["eval_release_id"] == "eval-20260905-v1"
    validate_manifest(manifest, expected_config_keys=_keys(), expected_environment="staging")


def test_manifest_v2_rejects_sensitive_config_key() -> None:
    digest = "sha256:" + "e" * 64
    with pytest.raises(ReleaseManifestError, match="敏感配置"):
        build_manifest_v2(
            environment="local",
            source_commit="local",
            image="local",
            config_keys=_keys(),
            prompts={"supervisor": "route"},
            safe_config={"deepseek_api_key": "must-not-pass"},
            model_artifacts={"embedding": {"digest": digest}},
            tool_schemas={},
            eval_release_id="eval-local",
            eval_dataset_digest=digest,
            eval_thresholds_digest=digest,
        )


def test_manifest_cli_builds_v2_from_component_file(tmp_path) -> None:
    config_file = tmp_path / "agent.env.example"
    config_file.write_text("AGENT_SERVICE_VERSION=0.1.0\n", encoding="utf-8")
    digest = "sha256:" + "f" * 64
    components_file = tmp_path / "components.json"
    components_file.write_text(
        json.dumps(
            {
                "prompts": {"supervisor": "route"},
                "safe_config": {"agent_max_tool_steps": 4},
                "model_artifacts": {"embedding": {"digest": digest}},
                "tool_schemas": {"fitness.user.get_current.v1": {"version": 1}},
                "eval_release_id": "eval-cli-v1",
                "eval_dataset_digest": digest,
                "eval_thresholds_digest": digest,
            }
        ),
        encoding="utf-8",
    )
    manifest_file = tmp_path / "manifest.json"

    assert (
        main(
            [
                "--manifest",
                str(manifest_file),
                "--config-contract",
                str(config_file),
                "--build",
                "--schema-version",
                "2",
                "--components",
                str(components_file),
                "--environment",
                "staging",
                "--source-commit",
                "a" * 40,
                "--image",
                f"registry.example.com/fitness-agent@{digest}",
            ]
        )
        == 0
    )
    assert json.loads(manifest_file.read_text(encoding="utf-8"))["schema_version"] == 2
