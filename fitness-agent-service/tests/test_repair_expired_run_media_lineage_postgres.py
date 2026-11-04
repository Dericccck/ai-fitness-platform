"""Exercise repair only in an explicitly designated disposable test database."""

import hashlib

from sqlalchemy import text

from scripts.repair_expired_run_media_lineage import count_candidates, repair_one
from tests import test_multimodal_dispatch_postgres as pg
from tests.test_ownership_recovery_postgres import build

pytestmark = pg.pytestmark
isolate_test_data = pg.isolate_test_data


async def test_only_recorded_deleted_source_with_expired_run_is_repaired():
    db = pg.database()
    try:
        _cipher, _repo, _old, _new, conversation, run, asset, _client = await build(db)
        async with db.engine.begin() as conn:
            await conn.execute(text("DELETE FROM agent_chat_runs WHERE run_id=:run"), {"run": run})
            await conn.execute(
                text(
                    "UPDATE agent_media_assets SET status='DELETED',deleted_at=CURRENT_TIMESTAMP WHERE asset_id=:asset"
                ),
                {"asset": asset},
            )
        assert await count_candidates(db) == 1
        assert await count_candidates(db) == 1  # counting is read-only
        assert await repair_one(db, "test-operator")
        assert await count_candidates(db) == 0
        assert not await repair_one(db, "test-operator")
        async with db.engine.connect() as conn:
            report = (
                await conn.execute(
                    text(
                        "SELECT deleted_at,report_ciphertext FROM agent_movement_reports WHERE run_id=:run"
                    ),
                    {"run": run},
                )
            ).one()
            epoch = (
                await conn.execute(
                    text(
                        "SELECT content_epoch FROM agent_chat_conversations WHERE conversation_id=:id"
                    ),
                    {"id": conversation},
                )
            ).scalar_one()
            key = (
                await conn.execute(
                    text("SELECT content_deleted FROM agent_chat_request_keys WHERE run_id=:run"),
                    {"run": run},
                )
            ).scalar_one()
            audit = (
                (
                    await conn.execute(
                        text(
                            "SELECT affected_runs FROM agent_lineage_repair_audit WHERE conversation_hash=:hash"
                        ),
                        {"hash": hashlib.sha256(conversation.encode()).hexdigest()},
                    )
                )
                .scalars()
                .all()
            )
        assert report.deleted_at is not None and report.report_ciphertext is None
        assert epoch == 1 and key is True
        assert audit == [1]
    finally:
        await db.close()


async def test_repair_ignores_expired_run_when_source_is_not_deleted():
    db = pg.database()
    try:
        _cipher, _repo, _old, _new, _conversation, run, _asset, _client = await build(db)
        async with db.engine.begin() as conn:
            await conn.execute(text("DELETE FROM agent_chat_runs WHERE run_id=:run"), {"run": run})
        assert await count_candidates(db) == 0
        assert not await repair_one(db, "test-operator")
    finally:
        await db.close()
