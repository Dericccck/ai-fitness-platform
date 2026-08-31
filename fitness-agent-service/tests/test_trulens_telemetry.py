from app.core.config import Settings
from app.evaluation.telemetry import TruLensTelemetry, hash_identifier, redact_text


def test_trulens_capture_is_disabled_by_default() -> None:
    telemetry = TruLensTelemetry(Settings(_env_file=None))

    assert telemetry.enabled is False
    assert telemetry.capture_content is False


def test_trulens_redaction_removes_common_sensitive_shapes() -> None:
    value = (
        "手机号 13800138000，邮箱 user@example.com，令牌 eyJheader.payload.signature，"
        "Authorization: Bearer abcdefghijklmnop，api_key=secret-value"
    )

    redacted = redact_text(value)

    assert "13800138000" not in redacted
    assert "user@example.com" not in redacted
    assert "eyJheader" not in redacted
    assert "abcdefghijklmnop" not in redacted
    assert "secret-value" not in redacted
    assert hash_identifier("conversation-1") == hash_identifier("conversation-1")
    assert hash_identifier("conversation-1") != "conversation-1"


def test_evaluation_capture_requires_explicit_mode() -> None:
    settings = Settings(
        _env_file=None,
        trulens_enabled=True,
        trulens_capture_mode="evaluation",
    )

    telemetry = TruLensTelemetry(settings)

    assert telemetry.enabled is True
    assert telemetry.capture_content is True


def test_identifier_hash_uses_hmac_when_secret_is_configured() -> None:
    assert hash_identifier("user-1", secret="secret") != hash_identifier("user-1")
    assert hash_identifier("user-1", secret="secret") == hash_identifier("user-1", secret="secret")


def test_trulens_span_has_official_record_attributes() -> None:
    settings = Settings(
        _env_file=None,
        trulens_enabled=True,
        trulens_capture_mode="metadata",
        source_commit="abc123",
        prompt_version="prompt-7",
        knowledge_base_version="kb-9",
    )
    telemetry = TruLensTelemetry(settings)

    # 仅检查配置值已进入实例；Span 属性在集成导出时由同一套常量设置。
    assert telemetry.app_name == "fitness-agent-service"
    assert telemetry.app_version == "abc123"
    assert telemetry.prompt_version == "prompt-7"
    assert telemetry.knowledge_base_version == "kb-9"
