from app.core.config import Settings
from app.evaluation.telemetry import TruLensTelemetry, hash_identifier, redact_text


def test_trulens_capture_is_disabled_by_default() -> None:
    telemetry = TruLensTelemetry(Settings(_env_file=None))

    assert telemetry.enabled is False
    assert telemetry.capture_content is False


def test_trulens_redaction_removes_common_sensitive_shapes() -> None:
    value = "手机号 13800138000，邮箱 user@example.com，令牌 eyJheader.payload.signature"

    redacted = redact_text(value)

    assert "13800138000" not in redacted
    assert "user@example.com" not in redacted
    assert "eyJheader" not in redacted
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
