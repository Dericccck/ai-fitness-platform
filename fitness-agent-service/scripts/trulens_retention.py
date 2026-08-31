"""按保留期限清理 TruLens 评测库中的记录、反馈和 OTEL 事件。"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime

from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings


def purge_expired(database_url: str, retention_days: int) -> dict[str, int]:
    """只操作 TruLens 默认表前缀，绝不连接 Agent 业务库。"""

    if retention_days < 1:
        raise ValueError("TruLens 保留天数必须大于 0")
    # 服务进程使用 asyncpg；清理 Worker 是同步脚本，必须切换到 psycopg 驱动。
    database_url = database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    engine = create_engine(database_url)
    cutoff_epoch = time.time() - retention_days * 86400
    cutoff_datetime = datetime.fromtimestamp(cutoff_epoch, tz=UTC).replace(tzinfo=None)
    tables = set(inspect(engine).get_table_names())
    deleted: dict[str, int] = {}
    with engine.begin() as connection:
        # 先清理独立事件表；记录表删除会通过 TruLens 外键级联反馈结果。
        if "trulens_events" in tables:
            result = connection.execute(
                text('DELETE FROM "trulens_events" WHERE "timestamp" < :cutoff'),
                {"cutoff": cutoff_datetime},
            )
            deleted["trulens_events"] = result.rowcount or 0
        if "trulens_records" in tables:
            result = connection.execute(
                text('DELETE FROM "trulens_records" WHERE "ts" < :cutoff'),
                {"cutoff": cutoff_epoch},
            )
            deleted["trulens_records"] = result.rowcount or 0
    engine.dispose()
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--days", type=int, default=None)
    args = parser.parse_args()
    settings = get_settings()
    database_url = args.database_url or settings.trulens_database_url
    days = args.days or settings.trulens_retention_days
    if database_url.strip() in {
        settings.database_url.strip(),
        settings.checkpoint_database_url.strip(),
    }:
        raise SystemExit("拒绝把 TruLens 保留任务指向 Agent 业务库或 Checkpoint 库")
    print(
        {
            "database": "独立评测库",
            "retention_days": days,
            "deleted": purge_expired(database_url, days),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
