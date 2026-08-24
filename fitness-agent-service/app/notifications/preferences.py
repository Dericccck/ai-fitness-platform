"""站内通知偏好和发布策略。

通知 Outbox 只保证“事件不会因为 API 进程退出而丢失”，它不应该直接决定是否可以
打扰用户。这个模块把用户授权、安静时间和频率限制独立出来，Worker 在最终发布前
按“用户 + 机构 + 通知类型”读取策略。

当前第一阶段只控制站内通知，但数据模型预留了通知类型边界。以后接入短信、Push
或 RabbitMQ 时，各渠道仍然应该复用同一套策略判断，避免不同渠道各自实现一套权限。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text

from .outbox import NotificationOutboxRecord

NotificationPolicyAction = Literal["ALLOW", "DEFER", "SUPPRESS"]
DEFAULT_NOTIFICATION_TYPE = "MEMORY_CANDIDATE_PENDING"
SUPPORTED_NOTIFICATION_TYPES = frozenset(
    {
        "MEMORY_CANDIDATE_PENDING",
        "APPOINTMENT_CREATED",
        "APPOINTMENT_RESCHEDULED",
        "APPOINTMENT_CANCELLED",
        "TRAINING_PLAN_PUBLISHED",
        "TRAINING_PLAN_REVIEW_REQUIRED",
    }
)


@dataclass(frozen=True)
class NotificationPreferenceRecord:
    """一个用户在一个机构内对一种通知的最终偏好。"""

    subject_user_id: str
    organization_id: str
    notification_type: str
    enabled: bool
    quiet_start: time | None
    quiet_end: time | None
    timezone: str
    minimum_interval_seconds: int
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class NotificationPolicyDecision:
    """Worker 最终发布前的策略结果。"""

    action: NotificationPolicyAction
    reason: str | None = None
    available_at: datetime | None = None


class NotificationPreferenceValidationError(ValueError):
    """通知偏好不满足安全约束。"""


class NotificationPreferenceRepository:
    """保存偏好并计算发布策略。

    偏好不存在时返回默认策略：允许站内通知、不设置安静时间、不限制频率。这样新增
    通知类型不会因为忘记给所有用户预置一行配置而静默丢失；用户显式保存后，数据库
    中才会留下覆盖默认值的记录。
    """

    def __init__(self, *, default_timezone: str = "Asia/Shanghai") -> None:
        _validate_timezone(default_timezone)
        self.default_timezone = default_timezone

    async def get(
        self,
        connection: Any,
        *,
        subject_user_id: str,
        organization_id: str,
        notification_type: str,
    ) -> NotificationPreferenceRecord:
        """读取用户偏好；没有显式配置时返回不落库的默认值。"""

        row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT * FROM agent_notification_preferences
                        WHERE subject_user_id = :subject_user_id
                          AND organization_id = :organization_id
                          AND notification_type = :notification_type
                        """
                    ),
                    {
                        "subject_user_id": subject_user_id,
                        "organization_id": organization_id,
                        "notification_type": notification_type,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return NotificationPreferenceRecord(
                subject_user_id=subject_user_id,
                organization_id=organization_id,
                notification_type=notification_type,
                enabled=True,
                quiet_start=None,
                quiet_end=None,
                timezone=self.default_timezone,
                minimum_interval_seconds=0,
                created_at=None,
                updated_at=None,
            )
        return _from_row(row)

    async def upsert(
        self,
        connection: Any,
        *,
        subject_user_id: str,
        organization_id: str,
        notification_type: str,
        enabled: bool,
        quiet_start: time | None,
        quiet_end: time | None,
        timezone: str,
        minimum_interval_seconds: int,
    ) -> NotificationPreferenceRecord:
        """保存用户显式设置，并在数据库返回最终值。

        这是用户在设置页面的明确操作，不是模型隐式写入业务事实，因此不走 Agent
        ``interrupt()``；但仍然校验主体范围、通知类型、时区和时间窗口。
        """

        _validate_preference(
            notification_type=notification_type,
            quiet_start=quiet_start,
            quiet_end=quiet_end,
            timezone=timezone,
            minimum_interval_seconds=minimum_interval_seconds,
        )
        row = (
            (
                await connection.execute(
                    text(
                        """
                        INSERT INTO agent_notification_preferences (
                            subject_user_id, organization_id, notification_type,
                            enabled, quiet_start, quiet_end, timezone,
                            minimum_interval_seconds
                        ) VALUES (
                            :subject_user_id, :organization_id, :notification_type,
                            :enabled, :quiet_start, :quiet_end, :timezone,
                            :minimum_interval_seconds
                        )
                        ON CONFLICT (subject_user_id, organization_id, notification_type)
                        DO UPDATE SET
                            enabled = EXCLUDED.enabled,
                            quiet_start = EXCLUDED.quiet_start,
                            quiet_end = EXCLUDED.quiet_end,
                            timezone = EXCLUDED.timezone,
                            minimum_interval_seconds = EXCLUDED.minimum_interval_seconds,
                            updated_at = CURRENT_TIMESTAMP
                        RETURNING *
                        """
                    ),
                    {
                        "subject_user_id": subject_user_id,
                        "organization_id": organization_id,
                        "notification_type": notification_type,
                        "enabled": enabled,
                        "quiet_start": quiet_start,
                        "quiet_end": quiet_end,
                        "timezone": timezone,
                        "minimum_interval_seconds": minimum_interval_seconds,
                    },
                )
            )
            .mappings()
            .one()
        )
        return _from_row(row)

    async def evaluate(
        self,
        connection: Any,
        *,
        record: NotificationOutboxRecord,
        now: datetime | None = None,
    ) -> NotificationPolicyDecision:
        """在发布事务中判断允许、延迟或抑制。

        判断与收件箱写入位于同一个数据库事务中。这样即使多个 Worker 并行处理，也不会
        在“刚检查完频率限制”与“写入通知”之间产生明显竞态。Outbox 只会保存状态结果，
        不会把用户的通知偏好正文复制到 payload。
        """

        preference = await self.get(
            connection,
            subject_user_id=record.subject_user_id,
            organization_id=record.organization_id,
            notification_type=record.notification_type,
        )
        if not preference.enabled:
            return NotificationPolicyDecision(action="SUPPRESS", reason="USER_DISABLED")

        current = _as_utc(now or datetime.now(UTC))
        quiet_end = _quiet_end(current, preference)
        if quiet_end is not None:
            return NotificationPolicyDecision(
                action="DEFER", reason="QUIET_HOURS", available_at=quiet_end
            )

        if preference.minimum_interval_seconds > 0:
            recent = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT created_at
                            FROM agent_in_app_notifications
                            WHERE subject_user_id = :subject_user_id
                              AND organization_id = :organization_id
                              AND notification_type = :notification_type
                            ORDER BY created_at DESC
                            LIMIT 1
                            """
                        ),
                        {
                            "subject_user_id": record.subject_user_id,
                            "organization_id": record.organization_id,
                            "notification_type": record.notification_type,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if recent is not None:
                recent_at = _as_utc(recent["created_at"])
                next_allowed = recent_at + timedelta(seconds=preference.minimum_interval_seconds)
                if next_allowed > current:
                    return NotificationPolicyDecision(action="SUPPRESS", reason="FREQUENCY_LIMIT")

        return NotificationPolicyDecision(action="ALLOW")


def _validate_preference(
    *,
    notification_type: str,
    quiet_start: time | None,
    quiet_end: time | None,
    timezone: str,
    minimum_interval_seconds: int,
) -> None:
    if notification_type not in SUPPORTED_NOTIFICATION_TYPES:
        raise NotificationPreferenceValidationError("unsupported notification type")
    if (quiet_start is None) != (quiet_end is None):
        raise NotificationPreferenceValidationError(
            "quiet start and quiet end must be configured together"
        )
    if quiet_start is not None and quiet_start == quiet_end:
        raise NotificationPreferenceValidationError("quiet window cannot be zero length")
    _validate_timezone(timezone)
    if minimum_interval_seconds < 0 or minimum_interval_seconds > 7 * 24 * 3600:
        raise NotificationPreferenceValidationError(
            "minimum notification interval must be between 0 and 7 days"
        )


def _validate_timezone(timezone: str) -> None:
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise NotificationPreferenceValidationError(
            "timezone must be a valid IANA timezone"
        ) from exc


def _quiet_end(now: datetime, preference: NotificationPreferenceRecord) -> datetime | None:
    """返回当前安静窗口结束时间；支持跨午夜窗口，例如 22:00-08:00。"""

    if preference.quiet_start is None or preference.quiet_end is None:
        return None
    local_now = now.astimezone(ZoneInfo(preference.timezone))
    current_time = local_now.timetz().replace(tzinfo=None)
    start = preference.quiet_start
    end = preference.quiet_end
    in_window = (start < end and start <= current_time < end) or (
        start > end and (current_time >= start or current_time < end)
    )
    if not in_window:
        return None
    end_date = local_now.date()
    if start > end and current_time >= start:
        end_date += timedelta(days=1)
    local_end = datetime.combine(end_date, end, tzinfo=local_now.tzinfo)
    return local_end.astimezone(UTC)


def _from_row(row: Any) -> NotificationPreferenceRecord:
    return NotificationPreferenceRecord(
        subject_user_id=str(row["subject_user_id"]),
        organization_id=str(row["organization_id"]),
        notification_type=str(row["notification_type"]),
        enabled=bool(row["enabled"]),
        quiet_start=row["quiet_start"],
        quiet_end=row["quiet_end"],
        timezone=str(row["timezone"]),
        minimum_interval_seconds=int(row["minimum_interval_seconds"]),
        created_at=_as_utc(row["created_at"]) if row["created_at"] else None,
        updated_at=_as_utc(row["updated_at"]) if row["updated_at"] else None,
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
