from datetime import UTC, datetime

import pytest

from app.notifications.templates import (
    NotificationTemplateRecord,
    NotificationTemplateValidationError,
    _validate_template,
    render_notification_template,
)


def _template(**overrides: object) -> NotificationTemplateRecord:
    values: dict[str, object] = {
        "template_key": "MEMORY_CANDIDATE_PENDING",
        "channel": "IN_APP",
        "version": 1,
        "status": "PUBLISHED",
        "title_template": "有一条待确认通知",
        "body_template": "请打开健身助手审核。",
        "variables": (),
        "created_by": "SYSTEM",
        "approved_by": "SYSTEM",
        "published_at": datetime.now(UTC),
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    values.update(overrides)
    return NotificationTemplateRecord(**values)  # type: ignore[arg-type]


def test_notification_template_renders_only_declared_variables() -> None:
    template = _template(
        title_template="处理 {notification_type}",
        body_template="聚合对象：{aggregate_id}",
        variables=("notification_type", "aggregate_id"),
    )

    assert render_notification_template(
        template,
        values={"notification_type": "MEMORY_CANDIDATE_PENDING", "aggregate_id": "candidate-1"},
    ) == ("处理 MEMORY_CANDIDATE_PENDING", "聚合对象：candidate-1")


def test_notification_template_rejects_undeclared_or_unsafe_variables() -> None:
    with pytest.raises(NotificationTemplateValidationError):
        _validate_template(
            template_key="MEMORY_CANDIDATE_PENDING",
            channel="IN_APP",
            title_template="审核 {aggregate_id}",
            body_template="正文",
            variables=(),
            created_by="admin-1",
        )

    with pytest.raises(NotificationTemplateValidationError):
        _validate_template(
            template_key="MEMORY_CANDIDATE_PENDING",
            channel="IN_APP",
            title_template="审核 {aggregate_id.value}",
            body_template="正文",
            variables=("aggregate_id.value",),
            created_by="admin-1",
        )


def test_notification_template_rejects_values_not_declared_by_template() -> None:
    with pytest.raises(NotificationTemplateValidationError):
        render_notification_template(
            _template(),
            values={"aggregate_id": "candidate-1"},
        )
