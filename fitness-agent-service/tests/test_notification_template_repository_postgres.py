"""需要显式开启的通知模板版本、幂等和审计 PostgreSQL 契约测试。"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.infrastructure.database import Database
from app.notifications.templates import (
    NotificationTemplateRepository,
    NotificationTemplateValidationError,
)

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_POSTGRES_TESTS") != "1",
    reason="需要显式设置 AGENT_RUN_POSTGRES_TESTS=1，并提供已迁移的 PostgreSQL",
)


async def test_notification_template_operations_are_idempotent_and_audited() -> None:
    database = Database(Settings(_env_file=None))
    repository = NotificationTemplateRepository()
    template_key = f"TEST_TEMPLATE_{uuid4().hex}"
    create_operation = f"create:{template_key}"
    approve_operation = f"approve:{template_key}"
    publish_operation = f"publish:{template_key}"
    try:
        async with database.engine.begin() as connection:
            draft = await repository.create_draft(
                connection,
                template_key=template_key,
                channel="IN_APP",
                title_template="测试通知",
                body_template="测试正文",
                created_by="admin-1",
                operation_id=create_operation,
            )
            replayed_draft = await repository.create_draft(
                connection,
                template_key=template_key,
                channel="IN_APP",
                title_template="不同正文不会覆盖已提交版本",
                body_template="不同正文",
                created_by="admin-1",
                operation_id=create_operation,
            )
            assert replayed_draft.version == draft.version
            assert replayed_draft.body_template == "测试正文"

            with pytest.raises(NotificationTemplateValidationError):
                await repository.approve(
                    connection,
                    template_key=template_key,
                    channel="IN_APP",
                    version=draft.version,
                    approved_by="admin-1",
                    operation_id=f"approve-self:{template_key}",
                )

            approved = await repository.approve(
                connection,
                template_key=template_key,
                channel="IN_APP",
                version=draft.version,
                approved_by="admin-2",
                operation_id=approve_operation,
            )
            replayed_approval = await repository.approve(
                connection,
                template_key=template_key,
                channel="IN_APP",
                version=draft.version,
                approved_by="admin-2",
                operation_id=approve_operation,
            )
            assert approved.status == "APPROVED"
            assert replayed_approval.status == "APPROVED"

            published = await repository.publish(
                connection,
                template_key=template_key,
                channel="IN_APP",
                version=draft.version,
                published_by="admin-3",
                operation_id=publish_operation,
            )
            replayed_publish = await repository.publish(
                connection,
                template_key=template_key,
                channel="IN_APP",
                version=draft.version,
                published_by="admin-3",
                operation_id=publish_operation,
            )
            assert published.status == "PUBLISHED"
            assert replayed_publish.status == "PUBLISHED"

            events = await repository.list_events(
                connection,
                template_key=template_key,
                channel="IN_APP",
                version=draft.version,
            )
            assert [event.event_type for event in events] == [
                "DRAFT_CREATED",
                "APPROVED",
                "PUBLISHED",
            ]

            with pytest.raises(NotificationTemplateValidationError):
                await repository.publish(
                    connection,
                    template_key=template_key,
                    channel="IN_APP",
                    version=draft.version,
                    published_by="admin-3",
                    operation_id=approve_operation,
                )
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM agent_notification_template_events "
                    "WHERE template_key = :template_key"
                ),
                {"template_key": template_key},
            )
            await connection.execute(
                text("DELETE FROM agent_notification_templates WHERE template_key = :template_key"),
                {"template_key": template_key},
            )
        await database.close()
