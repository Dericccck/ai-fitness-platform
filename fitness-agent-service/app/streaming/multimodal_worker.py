"""多模态任务执行器；认证恢复由可信会话服务注入，不接受客户端身份快照。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import structlog

from app.agent.movement_analysis import MovementAnalysisService
from app.core.multimodal_metrics import MultimodalMetrics
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.model_usage import collect_usage
from app.infrastructure.multimodal_gateway import (
    AuthorizedMediaRef,
    MultimodalOutcomeUnknown,
    MultimodalResponseError,
    MultimodalUnavailable,
)
from app.media.repository import MediaAccessError, MediaRepository
from app.media.storage import MediaStorageError, OssMediaStorage, StoredVersion
from app.streaming.control import run_owner
from app.streaming.durable_multimodal import (
    DispatchLease,
    DurableAccessError,
    DurableMultimodalRepository,
    MemoryRevisionChanged,
)
from app.streaming.legacy_bridge import LegacyConversationBridge, LegacyConversationUnavailable

logger = structlog.get_logger(__name__)
Reauthorize = Callable[[str], Awaitable[AgentIdentity | None]]


class MultimodalWorker:
    def __init__(
        self,
        repository: DurableMultimodalRepository,
        media: MediaRepository,
        storage: OssMediaStorage,
        analysis: MovementAnalysisService,
        reauthorize: Reauthorize,
        *,
        metrics: MultimodalMetrics | None = None,
        legacy_bridge: LegacyConversationBridge | None = None,
    ) -> None:
        self.repository = repository
        self.media = media
        self.storage = storage
        self.analysis = analysis
        self.reauthorize = reauthorize
        self.metrics = metrics
        self.legacy_bridge = legacy_bridge

    async def _identity(self, lease: DispatchLease) -> AgentIdentity | None:
        identity = await self.reauthorize(lease.auth_session_ref)
        return (
            identity
            if identity
            and identity.expires_at > time.time()
            and run_owner(identity) == lease.owner_hash
            else None
        )

    async def _execute(self, lease: DispatchLease) -> None:
        dispatched = False
        outcome = "interrupted"
        started = time.monotonic()
        try:
            identity = await self._identity(lease)
            if identity is None:
                await self.repository.fail(lease, outcome_unknown=False, code="AUTH_EXPIRED")
                outcome = "auth_expired"
                return
            references: list[AuthorizedMediaRef] = []
            for item in lease.payload["manifest"]:
                asset = await self.media.ready_asset(identity, item["asset_id"])
                if asset.revision != item["revision"] or asset.final_version != item["version"]:
                    raise MediaAccessError("素材版本已改变")
                if asset.kind not in {"image", "video"}:
                    raise MediaAccessError("素材类型无效")
                stored = StoredVersion(
                    asset.final_key, asset.final_version, asset.size, asset.content_type
                )
                url = await asyncio.to_thread(self.storage.signed_read_url, stored, ttl_seconds=600)
                references.append(
                    AuthorizedMediaRef(
                        asset.asset_id,
                        asset.revision,
                        "image" if asset.kind == "image" else "video",
                        url,
                        asset.duration_seconds,
                    )
                )
            # 若权限在解析素材期间被撤销，不再调用模型。
            identity = await self._identity(lease)
            if identity is None:
                await self.repository.fail(lease, outcome_unknown=False, code="AUTH_EXPIRED")
                outcome = "auth_expired"
                return
            revision = await self.repository.memory_revision(identity.subject)
            history = await self.repository.recent_context(lease, memory_revision=revision)
            legacy_thread = await self.repository.legacy_thread(
                identity, lease.payload["conversation_id"]
            )
            if legacy_thread:
                if self.legacy_bridge is None:
                    raise LegacyConversationUnavailable("历史对话暂时不可用")
                legacy = await self.legacy_bridge.history_by_thread(
                    identity, legacy_thread, memory_revision=revision
                )
                history = [*legacy, *history][-8:]
            if not await self.repository.mark_provider_started(lease, memory_revision=revision):
                return
            dispatched = True
            with collect_usage(lease.run_id, run_id=lease.run_id):
                report = await self.analysis.analyze(
                    identity=identity,
                    question=lease.payload["question"],
                    media=references,
                    exercise_hint=lease.payload["exercise_hint"] or None,
                    history=history,
                )
            if await self._identity(lease) is None:
                await self.repository.fail(lease, outcome_unknown=False, code="AUTH_EXPIRED")
                outcome = "auth_expired"
                return
            published = await self.repository.publish(lease, report)
            outcome = "completed" if published else "fenced"
        except MemoryRevisionChanged:
            outcome = "memory_changed"
            await self.repository.fail(lease, outcome_unknown=False, code="MEMORY_CHANGED")
        except (
            DurableAccessError,
            MediaAccessError,
            MediaStorageError,
            LegacyConversationUnavailable,
        ):
            outcome = "media_unavailable"
            await self.repository.fail(lease, outcome_unknown=False, code="MEDIA_UNAVAILABLE")
        except MultimodalOutcomeUnknown:
            outcome = "unknown"
            await self.repository.fail(lease, outcome_unknown=True, code="PROVIDER_OUTCOME_UNKNOWN")
        except (MultimodalResponseError, MultimodalUnavailable):
            outcome = "model_failed"
            await self.repository.fail(lease, outcome_unknown=False, code="MODEL_FAILED")
        except Exception as exc:  # noqa: BLE001 - 不把用户数据、URL 或供应商响应写入日志
            outcome = "unknown" if dispatched else "internal_error"
            logger.error("multimodal_worker_failed", error_type=type(exc).__name__)
            await self.repository.fail(
                lease,
                outcome_unknown=dispatched,
                code="PROVIDER_OUTCOME_UNKNOWN" if dispatched else "INTERNAL_ERROR",
            )
        finally:
            if self.metrics:
                self.metrics.record_run(outcome, time.monotonic() - started)

    async def run_once(self) -> bool:
        lease = await self.repository.claim()
        if lease is None:
            return False
        task = asyncio.create_task(self._execute(lease))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=10)
                if done:
                    await task
                    return True
                if not await self.repository.heartbeat(lease):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return True
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
