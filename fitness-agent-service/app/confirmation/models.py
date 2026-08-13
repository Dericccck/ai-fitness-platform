"""写操作确认领域模型和状态机。

这里故意不保存明文工具参数和 Confirmation Token。确认主表只保存确定性展示摘要、
参数哈希以及由上层加密后的执行参数密文；Token 只在一次运行的非持久化上下文中
短暂存在。这样 LangGraph Checkpoint、普通日志和确认事件都不会成为密钥或敏感参数
的旁路存储。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

# 授权状态描述“用户是否允许这次动作继续”，不能用来表示 Java 业务是否已经成功。
# PENDING 待确认；APPROVED 已批准；REJECTED 已拒绝；EXPIRED 已过期；CANCELLED 已撤销。
AuthorizationStatus = Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED", "CANCELLED"]

# 决定值是 API 输入，不是确认单最终状态；服务端会把它转换成 APPROVED 或 REJECTED。
ConfirmationDecision = Literal["APPROVE", "REJECT"]

# 执行状态描述“已批准动作实际执行到哪一步”。批准不会自动变成执行成功。
# NOT_STARTED 未领取执行权；RUNNING 执行中；SUCCEEDED 成功；
# FAILED_RETRYABLE 可重试失败；FAILED_FINAL 不可自动重试的最终失败。
ExecutionStatus = Literal["NOT_STARTED", "RUNNING", "SUCCEEDED", "FAILED_RETRYABLE", "FAILED_FINAL"]

# 不可变事件用于审计时间线；事件不是可编辑的当前状态，顺序由数据库时间和自增 ID 确定。
ConfirmationEventType = Literal[
    "CREATED",  # 已创建待确认单
    "APPROVED",  # 用户批准授权
    "REJECTED",  # 用户拒绝授权
    "EXPIRED",  # 超过 TTL，动作不再允许执行
    "CANCELLED",  # 在执行领取前主动撤销
    "ISSUED",  # 服务端绑定一次性凭证 JTI
    "CLAIMED",  # 某个恢复请求领取执行权
    "CONSUMED",  # 一次性凭证已经消费
    "REQUEUED",  # 可重试失败重新排队，旧 JTI 已失效
    "EXECUTION_SUCCEEDED",  # 业务工具真实执行成功
    "EXECUTION_FAILED",  # 业务工具真实执行失败
]


class ConfirmationStateError(RuntimeError):
    """确认单状态或并发版本不允许当前操作。"""


@dataclass(frozen=True)
class ConfirmationAction:
    """待确认的确定性业务动作。

    ``payload_hash`` 必须由规范化参数计算得到，不能由模型自由生成。精确执行参数
    由调用方加密成 ``payload_ciphertext`` 后交给仓储，领域对象不接受明文 Payload，
    防止业务代码无意中把它写入日志或 Checkpoint。
    """

    tool_id: str
    organization_id: str
    action: str
    resource_type: str
    resource_id: str | None
    expected_resource_version: int | None
    request_id: str
    payload_hash: str
    risk_level: str
    display_summary: Mapping[str, Any]
    payload_ciphertext: bytes
    payload_key_version: str
    thread_id: str
    subject_user_id: str
    actor_roles: tuple[str, ...] = ()
    actor_organization_ids: tuple[str, ...] = ()
    trace_id: str | None = None


@dataclass(frozen=True)
class ConfirmationRecord:
    """持久化确认单的只读领域快照。"""

    id: str
    protocol_version: int
    thread_id: str
    subject_user_id: str
    organization_id: str
    tool_id: str
    risk_level: str
    action: str
    resource_type: str
    resource_id: str | None
    expected_resource_version: int | None
    request_id: str
    payload_hash: str
    display_summary: Mapping[str, Any]
    payload_ciphertext: bytes
    payload_key_version: str
    authorization_status: AuthorizationStatus
    execution_status: ExecutionStatus
    version: int
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    execution_started_at: datetime | None = None
    finished_at: datetime | None = None
    decision_request_id: str | None = None
    credential_jti: str | None = None
    credential_consumed_at: datetime | None = None
    last_error_code: str | None = None

    def is_expired(self, now: datetime) -> bool:
        """判断是否已经超过授权有效期。"""

        return now >= self.expires_at

    def approve(self, now: datetime, decision_request_id: str) -> ConfirmationRecord:
        """生成批准后的内存快照；数据库更新由仓储在行锁事务中完成。"""

        if self.authorization_status != "PENDING":
            raise ConfirmationStateError("only pending confirmations can be approved")
        if self.is_expired(now):
            raise ConfirmationStateError("expired confirmation cannot be approved")
        return self._replace(
            authorization_status="APPROVED",
            approved_at=now,
            decision_request_id=decision_request_id,
            version=self.version + 1,
        )

    def reject(self, now: datetime, decision_request_id: str) -> ConfirmationRecord:
        """生成拒绝后的内存快照。"""

        if self.authorization_status != "PENDING":
            raise ConfirmationStateError("only pending confirmations can be rejected")
        return self._replace(
            authorization_status="REJECTED",
            rejected_at=now,
            decision_request_id=decision_request_id,
            version=self.version + 1,
        )

    def expire(self, now: datetime) -> ConfirmationRecord:
        """把仍待确认且已超时的授权标记为过期。"""

        if (
            self.authorization_status not in {"PENDING", "APPROVED"}
            or self.execution_status != "NOT_STARTED"
            or self.credential_consumed_at is not None
            or not self.is_expired(now)
        ):
            raise ConfirmationStateError("confirmation is not eligible for expiry")
        return self._replace(
            authorization_status="EXPIRED",
            credential_jti=None,
            version=self.version + 1,
        )

    def cancel(self) -> ConfirmationRecord:
        """撤销尚未执行的批准或待确认动作。"""

        if self.authorization_status not in {"PENDING", "APPROVED"}:
            raise ConfirmationStateError("confirmation is not cancellable")
        if self.execution_status != "NOT_STARTED":
            raise ConfirmationStateError("started execution cannot be cancelled")
        return self._replace(
            authorization_status="CANCELLED",
            credential_jti=None,
            version=self.version + 1,
        )

    def claim_execution(self, now: datetime) -> ConfirmationRecord:
        """领取一次批准动作的执行权，防止两个恢复请求同时执行。"""

        if self.authorization_status != "APPROVED":
            raise ConfirmationStateError("only approved confirmations can execute")
        if self.is_expired(now):
            raise ConfirmationStateError("expired confirmation cannot execute")
        if self.execution_status != "NOT_STARTED":
            raise ConfirmationStateError("confirmation execution has already been claimed")
        if not self.credential_jti or self.credential_consumed_at is not None:
            raise ConfirmationStateError("confirmation credential is not available")
        return self._replace(
            execution_status="RUNNING",
            execution_started_at=now,
            credential_consumed_at=now,
            version=self.version + 1,
        )

    def issue_credential(self, credential_jti: str, now: datetime) -> ConfirmationRecord:
        """绑定一次性凭证标识；真实 Token 仍只在服务端运行时签发。"""

        if self.authorization_status != "APPROVED":
            raise ConfirmationStateError("only approved confirmations can issue credentials")
        if self.is_expired(now):
            raise ConfirmationStateError("expired confirmation cannot issue credentials")
        if self.credential_jti is not None:
            raise ConfirmationStateError("confirmation credential has already been issued")
        if not credential_jti.strip():
            raise ConfirmationStateError("credential jti is required")
        return self._replace(credential_jti=credential_jti, version=self.version + 1)

    def requeue_retryable(self) -> ConfirmationRecord:
        """允许可恢复的工具失败重新领取执行权。"""

        if self.execution_status != "FAILED_RETRYABLE":
            raise ConfirmationStateError("only retryable execution can be requeued")
        return self._replace(
            execution_status="NOT_STARTED",
            execution_started_at=None,
            finished_at=None,
            credential_jti=None,
            credential_consumed_at=None,
            last_error_code=None,
            version=self.version + 1,
        )

    def finish_success(self, now: datetime) -> ConfirmationRecord:
        """记录业务工具真实返回成功。"""

        if self.execution_status != "RUNNING":
            raise ConfirmationStateError("only running execution can succeed")
        return self._replace(
            execution_status="SUCCEEDED",
            finished_at=now,
            version=self.version + 1,
        )

    def finish_failure(self, now: datetime, error_code: str, retryable: bool) -> ConfirmationRecord:
        """记录执行失败；可重试失败不会篡改原始批准决定。"""

        if self.execution_status != "RUNNING":
            raise ConfirmationStateError("only running execution can fail")
        return self._replace(
            execution_status="FAILED_RETRYABLE" if retryable else "FAILED_FINAL",
            finished_at=now,
            last_error_code=error_code,
            version=self.version + 1,
        )

    def _replace(self, **changes: Any) -> ConfirmationRecord:
        values = self.__dict__ | changes
        return ConfirmationRecord(**values)


@dataclass(frozen=True)
class ConfirmationEvent:
    """确认单不可变事件，不包含 Token 和明文执行参数。"""

    id: int | None
    confirmation_id: str
    event_type: ConfirmationEventType
    actor_user_id: str | None
    request_id: str | None
    decision_request_id: str | None
    trace_id: str | None
    authorization_status: AuthorizationStatus
    execution_status: ExecutionStatus
    authorization_version: int
    actor_roles: tuple[str, ...]
    actor_organization_ids: tuple[str, ...]
    created_at: datetime | None = None
