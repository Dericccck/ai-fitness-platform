from datetime import UTC, datetime, time

import pytest

from app.notifications.preferences import (
    NotificationPreferenceRecord,
    NotificationPreferenceValidationError,
    _quiet_end,
    _validate_preference,
)


def _preference(**overrides: object) -> NotificationPreferenceRecord:
    values: dict[str, object] = {
        "subject_user_id": "user-1",
        "organization_id": "org-1",
        "notification_type": "MEMORY_CANDIDATE_PENDING",
        "enabled": True,
        "quiet_start": time(22, 0),
        "quiet_end": time(8, 0),
        "timezone": "Asia/Shanghai",
        "minimum_interval_seconds": 0,
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return NotificationPreferenceRecord(**values)  # type: ignore[arg-type]


def test_quiet_window_supports_cross_midnight() -> None:
    morning = _quiet_end(datetime(2026, 8, 13, 17, 30, tzinfo=UTC), _preference())
    assert morning == datetime(2026, 8, 14, 0, 0, tzinfo=UTC)

    evening = _quiet_end(datetime(2026, 8, 14, 14, 30, tzinfo=UTC), _preference())
    assert evening == datetime(2026, 8, 15, 0, 0, tzinfo=UTC)

    outside = _quiet_end(datetime(2026, 8, 14, 5, 0, tzinfo=UTC), _preference())
    assert outside is None


def test_quiet_window_without_boundaries_is_disabled() -> None:
    assert (
        _quiet_end(
            datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
            _preference(quiet_start=None, quiet_end=None),
        )
        is None
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"quiet_start": time(22, 0), "quiet_end": None},
        {"quiet_start": time(22, 0), "quiet_end": time(22, 0)},
        {"timezone": "Not/A-Timezone"},
        {"minimum_interval_seconds": 604801},
        {"notification_type": "UNKNOWN"},
    ],
)
def test_invalid_notification_preferences_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(NotificationPreferenceValidationError):
        _validate_preference(
            notification_type=kwargs.get("notification_type", "MEMORY_CANDIDATE_PENDING"),  # type: ignore[arg-type]
            quiet_start=kwargs.get("quiet_start", None),  # type: ignore[arg-type]
            quiet_end=kwargs.get("quiet_end", None),  # type: ignore[arg-type]
            timezone=kwargs.get("timezone", "Asia/Shanghai"),  # type: ignore[arg-type]
            minimum_interval_seconds=kwargs.get("minimum_interval_seconds", 0),  # type: ignore[arg-type]
        )
