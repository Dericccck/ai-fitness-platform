"""Repair only recorded media dependencies whose source is deleted and run expired.

Dry-run is the default. --execute irreversibly clears dependent ciphertext and
training links, so take a backup and approve the exact data operation separately.
Never run this against a live database without the deployment's change process.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from uuid import uuid4

from sqlalchemy import text

from app.core.config import Settings
from app.infrastructure.database import Database

# Only exact, already-recorded asset dependencies and missing run rows qualify.
# No inferred history is added and no OSS object is touched.
_CANDIDATE = """SELECT c.conversation_id,c.owner_hash FROM agent_chat_conversations c
 WHERE c.deleted_at IS NULL AND EXISTS (
   SELECT 1 FROM agent_chat_messages m
   JOIN agent_chat_message_media d ON d.message_id=m.message_id
   JOIN agent_media_assets a ON a.asset_id=d.asset_id
   WHERE m.conversation_id=c.conversation_id AND m.owner_hash=c.owner_hash
     AND a.conversation_id=c.conversation_id AND a.owner_hash=c.owner_hash
     AND a.deleted_at IS NOT NULL AND m.run_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM agent_chat_runs r WHERE r.run_id=m.run_id)
     AND (EXISTS (SELECT 1 FROM agent_chat_messages x WHERE x.conversation_id=c.conversation_id
                  AND x.owner_hash=c.owner_hash AND x.run_id=m.run_id AND x.deleted_at IS NULL)
       OR EXISTS (SELECT 1 FROM agent_movement_reports p WHERE p.conversation_id=c.conversation_id
                  AND p.owner_hash=c.owner_hash AND p.run_id=m.run_id AND p.deleted_at IS NULL))
 )"""


async def count_candidates(db: Database) -> int:
    async with db.engine.begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text("SET LOCAL statement_timeout='30s'"))
        return int(
            (
                await conn.execute(text("SELECT count(*) FROM (" + _CANDIDATE + ") candidates"))
            ).scalar_one()
        )


async def repair_one(db: Database, operator: str) -> bool:
    if not operator.strip() or len(operator) > 128:
        raise ValueError("operator identifier required")
    async with db.engine.begin() as conn:
        await conn.execute(text("SET LOCAL lock_timeout='5s'"))
        await conn.execute(text("SET LOCAL statement_timeout='30s'"))
        row = (
            (
                await conn.execute(
                    text(
                        _CANDIDATE
                        + " ORDER BY c.conversation_id LIMIT 1 FOR UPDATE OF c SKIP LOCKED"
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return False
        params = {"conversation": row["conversation_id"], "owner": row["owner_hash"]}
        runs = (
            (
                await conn.execute(
                    text("""SELECT DISTINCT m.run_id FROM agent_chat_messages m
          JOIN agent_chat_message_media d ON d.message_id=m.message_id
          JOIN agent_media_assets a ON a.asset_id=d.asset_id
          WHERE m.conversation_id=:conversation AND m.owner_hash=:owner
            AND a.conversation_id=:conversation AND a.owner_hash=:owner AND a.deleted_at IS NOT NULL
            AND m.run_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM agent_chat_runs r WHERE r.run_id=m.run_id)"""),
                    params,
                )
            )
            .scalars()
            .all()
        )
        if not runs:
            return False
        params["runs"] = list(runs)
        await conn.execute(
            text("""UPDATE agent_chat_conversations SET content_epoch=content_epoch+1,
          updated_at=CURRENT_TIMESTAMP WHERE conversation_id=:conversation AND owner_hash=:owner"""),
            params,
        )
        await conn.execute(
            text("""DELETE FROM agent_report_training_links WHERE report_id IN
          (SELECT report_id FROM agent_movement_reports WHERE conversation_id=:conversation
            AND owner_hash=:owner AND run_id=ANY(CAST(:runs AS TEXT[])))"""),
            params,
        )
        await conn.execute(
            text("""UPDATE agent_chat_messages SET content_ciphertext=NULL,key_version=NULL,
          status='DELETED',deleted_at=COALESCE(deleted_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP
          WHERE conversation_id=:conversation AND owner_hash=:owner AND run_id=ANY(CAST(:runs AS TEXT[]))"""),
            params,
        )
        await conn.execute(
            text("""UPDATE agent_movement_reports SET report_ciphertext=NULL,key_version=NULL,
          deleted_at=COALESCE(deleted_at,CURRENT_TIMESTAMP) WHERE conversation_id=:conversation
            AND owner_hash=:owner AND run_id=ANY(CAST(:runs AS TEXT[]))"""),
            params,
        )
        await conn.execute(
            text("""UPDATE agent_chat_request_keys SET content_deleted=true
          WHERE conversation_id=:conversation AND owner_hash=:owner AND run_id=ANY(CAST(:runs AS TEXT[]))"""),
            params,
        )
        await conn.execute(
            text("""INSERT INTO agent_lineage_repair_audit
            (event_id,operator_hash,conversation_hash,affected_runs) VALUES (:id,:operator,:conversation,:count)"""),
            {
                "id": str(uuid4()),
                "operator": hashlib.sha256(operator.encode()).hexdigest(),
                "conversation": hashlib.sha256(params["conversation"].encode()).hexdigest(),
                "count": len(runs),
            },
        )
        return True


async def _run(execute: bool, maximum: int, operator: str) -> None:
    database = Database(Settings(_env_file=None))
    try:
        before = await count_candidates(database)
        processed = 0
        if execute:
            while processed < maximum and await repair_one(database, operator):
                processed += 1
        print(
            json.dumps(
                {
                    "mode": "execute" if execute else "dry_run",
                    "candidate_conversations_before": before,
                    "processed_conversations": processed,
                    "candidate_conversations_after": await count_candidates(database),
                }
            )
        )
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually clear affected records; default is read-only",
    )
    parser.add_argument(
        "--operator", default="", help="maintenance operator identifier (required with --execute)"
    )
    parser.add_argument(
        "--maximum", type=int, default=100, help="maximum conversations to process (1-10000)"
    )
    args = parser.parse_args()
    if not os.environ.get("AGENT_DATABASE_URL", "").strip():
        parser.error("explicit AGENT_DATABASE_URL is required")
    if not 1 <= args.maximum <= 10000:
        parser.error("maximum must be between 1 and 10000")
    if args.execute and (not args.operator.strip() or len(args.operator) > 128):
        parser.error("--operator is required with --execute (maximum 128 characters)")
    asyncio.run(_run(args.execute, args.maximum, args.operator))


if __name__ == "__main__":
    main()
