"""使用官方 TruLens OTEL 导出器验收独立 PostgreSQL 评测库。"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings


def _sync_database_url(database_url: str) -> str:
    """把异步 SQLAlchemy 方言转换为同步 psycopg 方言。"""

    return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


def _write_real_span(database_url: str) -> str:
    """写入一条带完整版本关联的真实 OTEL Span，并返回 record_id。"""

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from trulens.core.database.connector.default import DefaultDBConnector
    from trulens.experimental.otel_tracing.core.exporter.connector import (
        TruLensOtelSpanExporter,
    )

    record_id = f"live-check-{uuid.uuid4().hex}"
    connector = DefaultDBConnector(database_url=database_url)
    exporter = TruLensOtelSpanExporter(connector)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("fitness-agent-service.trulens.live-check")
    with tracer.start_as_current_span("fitness.agent.request") as span:
        span.set_attribute("ai.observability.app_id", "fitness-agent-service")
        span.set_attribute("ai.observability.app_name", "fitness-agent-service")
        span.set_attribute("ai.observability.app_version", "live-check")
        span.set_attribute("ai.observability.span_type", "record_root")
        span.set_attribute("ai.observability.record_id", record_id)
        span.set_attribute("ai.observability.record_root.input", "本地 TruLens PostgreSQL 验收")
        span.set_attribute(
            "ai.observability.record_root.output", "真实 OTEL Span 已写入独立评测库。"
        )
        span.set_attribute("fitness.agent.code_version", "live-check")
        span.set_attribute("fitness.agent.prompt_version", "prompt-live-check")
        span.set_attribute("fitness.agent.model", "deepseek-live-check")
        span.set_attribute("fitness.agent.knowledge_base_version", "kb-live-check")
        span.set_attribute("fitness.agent.graph_version", "graph-live-check")
        span.set_attribute("fitness.agent.status", "SUCCEEDED")
    provider.force_flush()
    provider.shutdown()
    return record_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--execute", action="store_true", help="写入并读取一条真实验收 Trace")
    args = parser.parse_args()
    settings = get_settings()
    database_url = _sync_database_url(args.database_url or settings.trulens_database_url)
    if not database_url.startswith("postgresql+psycopg://"):
        print("TruLens PostgreSQL 验收需要 postgresql+psycopg:// 连接串")
        return 2
    try:
        engine = create_engine(database_url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        if not args.execute:
            print("[通过] TruLens PostgreSQL 连接正常（未写入测试 Trace）")
            return 0

        record_id = _write_real_span(database_url)
        tables = set(inspect(engine).get_table_names())
        if "trulens_events" not in tables:
            raise RuntimeError("官方 TruLens 导出后未发现 trulens_events 表")
        with engine.connect() as connection:
            event_count = connection.execute(
                text('SELECT COUNT(*) FROM "trulens_events"')
            ).scalar_one()
        print(
            {
                "passed": True,
                "record_id": record_id,
                "event_count": event_count,
                "checked_at": datetime.now(UTC).isoformat(),
                "database": "独立 TruLens PostgreSQL",
            }
        )
        return 0
    except (ImportError, OSError, RuntimeError, SQLAlchemyError) as exc:
        print(f"[失败] TruLens PostgreSQL 在线验收失败：{exc}")
        return 1
    finally:
        if "engine" in locals():
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
