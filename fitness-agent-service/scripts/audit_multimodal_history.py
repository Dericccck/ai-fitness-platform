"""Read-only aggregate audit for historical ownership and media lineage gaps.

This intentionally emits counts only. A candidate is not proof of missing lineage:
old data does not record which prior messages were actually sent to a model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from sqlalchemy import text

from app.core.config import Settings
from app.infrastructure.database import Database


async def audit(database: Database) -> dict[str, int]:
    async with database.engine.begin() as connection:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        await connection.execute(text("SET LOCAL statement_timeout = '30s'"))
        row = (
            (
                await connection.execute(
                    text("""
          WITH owners AS (
            SELECT subject_user_id, organization_scope, count(DISTINCT owner_hash) AS owner_count
            FROM agent_chat_conversations WHERE deleted_at IS NULL
            GROUP BY subject_user_id, organization_scope
          ), candidates AS (
            SELECT current_message.message_id
            FROM agent_chat_messages current_message
            WHERE current_message.role='assistant' AND current_message.deleted_at IS NULL
              AND current_message.status='COMMITTED' AND current_message.content_ciphertext IS NOT NULL
              AND current_message.run_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM agent_chat_message_media d
                WHERE d.message_id=current_message.message_id AND d.dependency_type='CONTEXT'
              )
              AND EXISTS (
                SELECT 1 FROM agent_chat_messages earlier
                JOIN agent_chat_message_media earlier_media ON earlier_media.message_id=earlier.message_id
                WHERE earlier.conversation_id=current_message.conversation_id
                  AND earlier.owner_hash=current_message.owner_hash
                  AND earlier.run_id<>current_message.run_id
                  AND earlier.created_at<=current_message.created_at
              )
          )
          SELECT (SELECT count(*) FROM owners WHERE owner_count>1) AS split_owner_groups,
                 (SELECT count(*) FROM candidates) AS possible_missing_lineage_reports
        """)
                )
            )
            .mappings()
            .one()
        )
        return {name: int(value) for name, value in row.items()}


async def _run() -> None:
    settings = Settings(_env_file=None)
    database = Database(settings)
    try:
        counts = await audit(database)
        print(
            json.dumps(
                {
                    "counts": counts,
                    "interpretation": "conservative_candidates_not_confirmed_dependencies",
                }
            )
        )
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="run read-only audit against AGENT_DATABASE_URL"
    )
    args = parser.parse_args()
    if not args.execute:
        parser.error("pass --execute after selecting the intended read-only database")
    if not os.environ.get("AGENT_DATABASE_URL", "").strip():
        parser.error("explicit AGENT_DATABASE_URL is required")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
