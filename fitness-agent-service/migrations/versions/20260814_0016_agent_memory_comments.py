"""补充长期健身 Memory 表级中文说明。"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260814_0016"
down_revision: str | None = "20260814_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """让 Navicat 和 PostgreSQL 元数据浏览器能直接显示表用途。"""

    op.execute(
        "COMMENT ON TABLE agent_memories IS "
        "'用户明确提供并确认过的低敏健身长期 Memory；不保存动态业务事实和医疗诊断'"
    )


def downgrade() -> None:
    """回滚表级说明，不影响 Memory 数据。"""

    op.execute("COMMENT ON TABLE agent_memories IS NULL")
