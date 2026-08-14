"""通知模板版本和渠道抽象。

通知事件只负责描述“发生了什么”，不应该把模型自由生成的正文直接写进收件箱。
模板由受控配置发布，Worker 在真正投递时读取已发布版本并保存渲染快照。这样模板
后续升级不会改写历史通知，也为未来接入短信、Push 或消息队列保留统一边界。
"""

from __future__ import annotations

import json
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text

NotificationChannel = Literal["IN_APP"]
NotificationTemplateStatus = Literal["DRAFT", "APPROVED", "PUBLISHED", "RETIRED"]
NotificationTemplateEventType = Literal["DRAFT_CREATED", "APPROVED", "PUBLISHED"]

_CHANNELS = frozenset({"IN_APP"})
_STATUSES = frozenset({"DRAFT", "APPROVED", "PUBLISHED", "RETIRED"})
_FORMATTER = string.Formatter()


class NotificationTemplateValidationError(ValueError):
    """通知模板不符合渠道或变量约束。"""


class NotificationTemplateNotFound(LookupError):
    """没有可用于投递的已发布通知模板。"""


@dataclass(frozen=True)
class NotificationTemplateRecord:
    """通知模板的受控版本；历史通知只保存该版本的渲染结果。"""

    template_key: str
    channel: str
    version: int
    status: str
    title_template: str
    body_template: str
    variables: tuple[str, ...]
    created_by: str
    approved_by: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class NotificationTemplateEventRecord:
    """模板生命周期审计摘要；不保存标题、正文或模板变量。"""

    id: int
    template_key: str
    channel: str
    version: int
    event_type: str
    actor_user_id: str
    status_after: str
    operation_id: str
    created_at: datetime


class NotificationTemplateRepository:
    """管理模板生命周期，并为发布器读取当前生效版本。"""

    async def get_published(
        self,
        connection: Any,
        *,
        template_key: str,
        channel: NotificationChannel = "IN_APP",
    ) -> NotificationTemplateRecord:
        """读取唯一生效模板；没有模板时 fail-closed，交给 Outbox 重试。"""

        _validate_key_and_channel(template_key, channel)
        row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT *
                        FROM agent_notification_templates
                        WHERE template_key = :template_key
                          AND channel = :channel
                          AND status = 'PUBLISHED'
                        """
                    ),
                    {"template_key": template_key, "channel": channel},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotificationTemplateNotFound(
                f"published notification template not found: {template_key}/{channel}"
            )
        return _from_row(row)

    async def create_draft(
        self,
        connection: Any,
        *,
        template_key: str,
        channel: NotificationChannel,
        title_template: str,
        body_template: str,
        variables: tuple[str, ...] = (),
        created_by: str,
        operation_id: str,
    ) -> NotificationTemplateRecord:
        """创建下一个版本的草稿；相同 operation_id 重试返回同一版本。"""

        _validate_operation_id(operation_id)
        await _lock_operation(connection, operation_id)
        replay = await self._replay_template_operation(
            connection,
            operation_id=operation_id,
            event_type="DRAFT_CREATED",
            template_key=template_key,
            channel=channel,
        )
        if replay is not None:
            return replay
        _validate_template(
            template_key=template_key,
            channel=channel,
            title_template=title_template,
            body_template=body_template,
            variables=variables,
            created_by=created_by,
        )
        # 同一模板键的版本号由数据库生成；事务级 advisory lock 避免两个管理员并发
        # 创建时同时算出相同的 MAX(version) + 1，最终互相撞上主键。
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"notification-template:{template_key}:{channel}"},
        )
        row = (
            (
                await connection.execute(
                    text(
                        """
                        INSERT INTO agent_notification_templates (
                            template_key, channel, version, status,
                            title_template, body_template, variables, created_by
                        )
                        SELECT :template_key, :channel,
                               COALESCE(MAX(version), 0) + 1, 'DRAFT',
                               :title_template, :body_template,
                               CAST(:variables AS JSONB), :created_by
                        FROM agent_notification_templates
                        WHERE template_key = :template_key AND channel = :channel
                        RETURNING *
                        """
                    ),
                    {
                        "template_key": template_key,
                        "channel": channel,
                        "title_template": title_template,
                        "body_template": body_template,
                        "variables": _json_array(variables),
                        "created_by": created_by,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise RuntimeError("notification template draft was not created")
        await self._insert_event(
            connection,
            template_key=template_key,
            channel=channel,
            version=int(row["version"]),
            event_type="DRAFT_CREATED",
            actor_user_id=created_by,
            status_after="DRAFT",
            operation_id=operation_id,
        )
        return _from_row(row)

    async def approve(
        self,
        connection: Any,
        *,
        template_key: str,
        channel: NotificationChannel,
        version: int,
        approved_by: str,
        operation_id: str,
    ) -> NotificationTemplateRecord:
        """把草稿变为已审核版本；相同 operation_id 重试返回同一结果。"""

        _validate_key_and_channel(template_key, channel)
        _validate_operation_id(operation_id)
        await _lock_operation(connection, operation_id)
        replay = await self._replay_template_operation(
            connection,
            operation_id=operation_id,
            event_type="APPROVED",
            template_key=template_key,
            channel=channel,
            version=version,
        )
        if replay is not None:
            return replay
        if version < 1 or not approved_by.strip():
            raise NotificationTemplateValidationError("template version and approver are required")
        row = (
            (
                await connection.execute(
                    text(
                        """
                        UPDATE agent_notification_templates
                        SET status = 'APPROVED', approved_by = :approved_by,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE template_key = :template_key AND channel = :channel
                          AND version = :version AND status = 'DRAFT'
                          AND created_by <> :approved_by
                        RETURNING *
                        """
                    ),
                    {
                        "template_key": template_key,
                        "channel": channel,
                        "version": version,
                        "approved_by": approved_by,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotificationTemplateValidationError("only DRAFT templates can be approved")
        await self._insert_event(
            connection,
            template_key=template_key,
            channel=channel,
            version=version,
            event_type="APPROVED",
            actor_user_id=approved_by,
            status_after="APPROVED",
            operation_id=operation_id,
        )
        return _from_row(row)

    async def publish(
        self,
        connection: Any,
        *,
        template_key: str,
        channel: NotificationChannel,
        version: int,
        published_by: str,
        operation_id: str,
    ) -> NotificationTemplateRecord:
        """发布已审核版本，并将同键旧版本退役；相同 operation_id 重试返回同一结果。"""

        _validate_key_and_channel(template_key, channel)
        _validate_operation_id(operation_id)
        await _lock_operation(connection, operation_id)
        replay = await self._replay_template_operation(
            connection,
            operation_id=operation_id,
            event_type="PUBLISHED",
            template_key=template_key,
            channel=channel,
            version=version,
        )
        if replay is not None:
            return replay
        if version < 1 or not published_by.strip():
            raise NotificationTemplateValidationError("template version must be positive")
        target = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT status
                        FROM agent_notification_templates
                        WHERE template_key = :template_key AND channel = :channel
                          AND version = :version
                        FOR UPDATE
                        """
                    ),
                    {
                        "template_key": template_key,
                        "channel": channel,
                        "version": version,
                    },
                )
            )
            .mappings()
            .first()
        )
        if target is None or target["status"] != "APPROVED":
            raise NotificationTemplateValidationError("only APPROVED templates can be published")
        retired_rows = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT version
                        FROM agent_notification_templates
                        WHERE template_key = :template_key AND channel = :channel
                          AND status = 'PUBLISHED' AND version <> :version
                        FOR UPDATE
                        """
                    ),
                    {"template_key": template_key, "channel": channel, "version": version},
                )
            )
            .mappings()
            .all()
        )
        await connection.execute(
            text(
                """
                UPDATE agent_notification_templates
                SET status = 'RETIRED', updated_at = CURRENT_TIMESTAMP
                WHERE template_key = :template_key AND channel = :channel
                  AND status = 'PUBLISHED' AND version <> :version
                """
            ),
            {"template_key": template_key, "channel": channel, "version": version},
        )
        row = (
            (
                await connection.execute(
                    text(
                        """
                        UPDATE agent_notification_templates
                        SET status = 'PUBLISHED', published_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE template_key = :template_key AND channel = :channel
                          AND version = :version AND status = 'APPROVED'
                        RETURNING *
                        """
                    ),
                    {
                        "template_key": template_key,
                        "channel": channel,
                        "version": version,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotificationTemplateValidationError("only APPROVED templates can be published")
        await self._insert_event(
            connection,
            template_key=template_key,
            channel=channel,
            version=version,
            event_type="PUBLISHED",
            actor_user_id=published_by,
            status_after="PUBLISHED",
            operation_id=operation_id,
            metadata={"retired_versions": [int(item["version"]) for item in retired_rows]},
        )
        return _from_row(row)

    async def list_events(
        self,
        connection: Any,
        *,
        template_key: str,
        channel: NotificationChannel,
        version: int,
        limit: int = 50,
    ) -> list[NotificationTemplateEventRecord]:
        """读取模板生命周期摘要，不返回模板正文或变量。"""

        _validate_key_and_channel(template_key, channel)
        if version < 1 or limit < 1 or limit > 100:
            raise ValueError("template event parameters are invalid")
        rows = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT *
                        FROM agent_notification_template_events
                        WHERE template_key = :template_key AND channel = :channel
                          AND version = :version
                        ORDER BY id
                        LIMIT :limit
                        """
                    ),
                    {
                        "template_key": template_key,
                        "channel": channel,
                        "version": version,
                        "limit": limit,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [_event_from_row(row) for row in rows]

    async def _replay_template_operation(
        self,
        connection: Any,
        *,
        operation_id: str,
        event_type: NotificationTemplateEventType,
        template_key: str,
        channel: NotificationChannel,
        version: int | None = None,
    ) -> NotificationTemplateRecord | None:
        """检查操作重试；同一幂等键不能被复用到其他模板动作。"""

        row = (
            (
                await connection.execute(
                    text(
                        "SELECT * FROM agent_notification_template_events WHERE operation_id = :operation_id"
                    ),
                    {"operation_id": operation_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        if (
            row["event_type"] != event_type
            or row["template_key"] != template_key
            or row["channel"] != channel
            or (version is not None and int(row["version"]) != version)
        ):
            raise NotificationTemplateValidationError(
                "operation_id was used for another template action"
            )
        template_row = (
            (
                await connection.execute(
                    text(
                        """
                        SELECT * FROM agent_notification_templates
                        WHERE template_key = :template_key AND channel = :channel AND version = :version
                        """
                    ),
                    {
                        "template_key": template_key,
                        "channel": channel,
                        "version": int(row["version"]),
                    },
                )
            )
            .mappings()
            .first()
        )
        if template_row is None:
            raise NotificationTemplateValidationError(
                "template for idempotent operation is missing"
            )
        return _from_row(template_row)

    async def _insert_event(
        self,
        connection: Any,
        *,
        template_key: str,
        channel: NotificationChannel,
        version: int,
        event_type: NotificationTemplateEventType,
        actor_user_id: str,
        status_after: NotificationTemplateStatus,
        operation_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """在模板状态变更同一事务内追加不可变事件。"""

        await connection.execute(
            text(
                """
                INSERT INTO agent_notification_template_events (
                    template_key, channel, version, event_type, actor_user_id,
                    status_after, operation_id, metadata
                ) VALUES (
                    :template_key, :channel, :version, :event_type, :actor_user_id,
                    :status_after, :operation_id, CAST(:metadata AS JSONB)
                )
                ON CONFLICT (operation_id) DO NOTHING
                """
            ),
            {
                "template_key": template_key,
                "channel": channel,
                "version": version,
                "event_type": event_type,
                "actor_user_id": actor_user_id,
                "status_after": status_after,
                "operation_id": operation_id,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )


def render_notification_template(
    template: NotificationTemplateRecord,
    *,
    values: dict[str, str],
) -> tuple[str, str]:
    """使用模板声明过的变量渲染标题和正文，不允许额外变量或对象属性访问。"""

    provided = set(values)
    declared = set(template.variables)
    if not provided <= declared:
        raise NotificationTemplateValidationError("template values contain undeclared variables")
    try:
        title = template.title_template.format_map(_StrictFormatValues(values))
        body = template.body_template.format_map(_StrictFormatValues(values))
    except (KeyError, ValueError, IndexError) as exc:
        raise NotificationTemplateValidationError("notification template rendering failed") from exc
    return title, body


class _StrictFormatValues(dict[str, str]):
    """禁止模板通过缺失键静默生成半截通知。"""

    def __missing__(self, key: str) -> str:
        raise KeyError(key)


def _validate_template(
    *,
    template_key: str,
    channel: str,
    title_template: str,
    body_template: str,
    variables: tuple[str, ...],
    created_by: str,
) -> None:
    _validate_key_and_channel(template_key, channel)
    if not created_by.strip() or not title_template.strip() or not body_template.strip():
        raise NotificationTemplateValidationError("template author and content are required")
    if len(title_template) > 200 or len(body_template) > 2000:
        raise NotificationTemplateValidationError("notification template is too long")
    if len(set(variables)) != len(variables) or any(not item.strip() for item in variables):
        raise NotificationTemplateValidationError("template variables must be unique and non-empty")
    fields = {
        field_name
        for source in (title_template, body_template)
        for _, field_name, _, _ in _FORMATTER.parse(source)
        if field_name is not None
    }
    if any(
        not field or not field.replace("_", "a").isalnum() or field[0].isdigit() for field in fields
    ):
        raise NotificationTemplateValidationError("template variables must be simple names")
    if fields - set(variables):
        raise NotificationTemplateValidationError("template contains undeclared variables")


def _validate_key_and_channel(template_key: str, channel: str) -> None:
    if not template_key.strip() or len(template_key) > 128:
        raise NotificationTemplateValidationError("template key is invalid")
    if channel not in _CHANNELS:
        raise NotificationTemplateValidationError("notification channel is not supported")


def _validate_operation_id(operation_id: str) -> None:
    if not operation_id.strip() or len(operation_id) > 128:
        raise NotificationTemplateValidationError("operation_id is invalid")


async def _lock_operation(connection: Any, operation_id: str) -> None:
    """串行化相同幂等键，避免两个并发请求都先查不到审计事件。"""

    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"notification-operation:{operation_id}"},
    )


def _from_row(row: Any) -> NotificationTemplateRecord:
    return NotificationTemplateRecord(
        template_key=str(row["template_key"]),
        channel=str(row["channel"]),
        version=int(row["version"]),
        status=str(row["status"]),
        title_template=str(row["title_template"]),
        body_template=str(row["body_template"]),
        variables=tuple(str(item) for item in (row["variables"] or [])),
        created_by=str(row["created_by"]),
        approved_by=str(row["approved_by"]) if row["approved_by"] else None,
        published_at=_as_utc(row["published_at"]),
        created_at=_required_utc(row["created_at"]),
        updated_at=_required_utc(row["updated_at"]),
    )


def _event_from_row(row: Any) -> NotificationTemplateEventRecord:
    return NotificationTemplateEventRecord(
        id=int(row["id"]),
        template_key=str(row["template_key"]),
        channel=str(row["channel"]),
        version=int(row["version"]),
        event_type=str(row["event_type"]),
        actor_user_id=str(row["actor_user_id"]),
        status_after=str(row["status_after"]),
        operation_id=str(row["operation_id"]),
        created_at=_required_utc(row["created_at"]),
    )


def _json_array(values: tuple[str, ...]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _required_utc(value: datetime) -> datetime:
    normalized = _as_utc(value)
    if normalized is None:
        raise RuntimeError("notification template required timestamp is NULL")
    return normalized
