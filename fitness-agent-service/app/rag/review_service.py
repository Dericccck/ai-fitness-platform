"""多角色健身知识专业审核用例服务。"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from secrets import token_hex

from app.infrastructure.agent_context import AgentIdentity

from .admin_models import (
    KnowledgeAdminForbidden,
    KnowledgeIngestionJob,
    KnowledgeJobTransitionError,
)
from .admin_repository import KnowledgeIngestionRepository
from .review import KnowledgeReviewReport
from .review_workflow import (
    KnowledgePublicationCredential,
    KnowledgeReviewDecision,
    KnowledgeReviewOutcome,
    KnowledgeReviewRegion,
    KnowledgeReviewRequirement,
    ReviewDecisionValue,
    ReviewDomain,
    ReviewScopeType,
    review_requirements,
    validate_decision_scope,
    validate_reviewer,
)
from .storage import DocumentStorage


@dataclass(frozen=True)
class KnowledgeReviewCase:
    """审核工作台所需的报告、范围、既有决定和发布状态。"""

    job: KnowledgeIngestionJob
    report: KnowledgeReviewReport
    requirements: tuple[KnowledgeReviewRequirement, ...]
    decisions: tuple[KnowledgeReviewDecision, ...]
    publication_credential: KnowledgePublicationCredential | None


class KnowledgeReviewService:
    """允许被签名能力选中的教练或专业人员提交终局审核决定。"""

    def __init__(self, jobs: KnowledgeIngestionRepository, storage: DocumentStorage) -> None:
        self.jobs = jobs
        self.storage = storage

    async def get_case(self, identity: AgentIdentity, job_id: str) -> KnowledgeReviewCase:
        """返回审核案件；调用者必须至少有一个待审领域的真实授权。"""

        job, report, requirements = await self._load_current_case(job_id)
        if not any(_can_review(identity, job, requirement) for requirement in requirements):
            # 这里返回统一禁止，不暴露某个任务究竟需要临床还是教练审核。
            validate_reviewer(identity, job, requirements[0])
        return KnowledgeReviewCase(
            job=job,
            report=report,
            requirements=requirements,
            decisions=tuple(await self.jobs.list_review_decisions(report.id)),
            publication_credential=await self.jobs.get_publication_credential(job.id),
        )

    async def read_source(
        self, identity: AgentIdentity, job_id: str
    ) -> tuple[KnowledgeIngestionJob, bytes]:
        """向获授权审核人返回不可变暂存原件，而非可被替换的外部 URL。"""

        case = await self.get_case(identity, job_id)
        content = await asyncio.to_thread(self.storage.read, case.job.storage_key)
        self._verify_staged_bytes(case.job, case.report, content)
        return case.job, content

    async def submit_decision(
        self,
        identity: AgentIdentity,
        job_id: str,
        *,
        review_domain: ReviewDomain,
        decision: ReviewDecisionValue,
        scope_type: ReviewScopeType,
        page_numbers: tuple[int, ...],
        regions: tuple[KnowledgeReviewRegion, ...],
        comment: str,
    ) -> KnowledgeReviewOutcome:
        """校验签名权限、职责分离和范围覆盖后保存不可变决定。"""

        job, report, requirements = await self._load_current_case(job_id)
        requirement = next((item for item in requirements if item.domain == review_domain), None)
        if requirement is None:
            raise ValueError("the selected review domain is not required by this report")
        validate_reviewer(identity, job, requirement)
        # 决定接口不能假设审核人一定先调用过原件下载接口。提交决定前再次读取并
        # 校验暂存对象，保证最终决定确实绑定报告中的文件字节。
        staged_content = await asyncio.to_thread(self.storage.read, job.storage_key)
        self._verify_staged_bytes(job, report, staged_content)
        total_pages = max((profile.page_number for profile in report.page_profiles), default=0)
        validate_decision_scope(
            requirement,
            scope_type,
            page_numbers,
            regions,
            total_pages=total_pages,
        )
        normalized_comment = comment.strip()
        if len(normalized_comment) < 10 or len(normalized_comment) > 2000:
            raise ValueError("review comment must contain 10 to 2000 characters")
        review_decision = KnowledgeReviewDecision(
            id=token_hex(16),
            report_id=report.id,
            job_id=job.id,
            review_domain=review_domain,
            decision=decision,
            scope_type=scope_type,
            page_numbers=page_numbers,
            regions=regions,
            finding_codes=requirement.finding_codes,
            reviewer_id=identity.subject,
            reviewer_roles=tuple(sorted(identity.roles)),
            reviewer_capabilities=tuple(sorted(identity.capabilities)),
            reviewer_qualifications=tuple(sorted(identity.qualifications)),
            reviewer_organization_ids=tuple(sorted(identity.organization_ids)),
            comment=normalized_comment,
        )
        return await self.jobs.record_review_decision(review_decision, report)

    async def _load_current_case(
        self, job_id: str
    ) -> tuple[
        KnowledgeIngestionJob,
        KnowledgeReviewReport,
        tuple[KnowledgeReviewRequirement, ...],
    ]:
        job = await self.jobs.get_job(job_id)
        if job.status != "PENDING_REVIEW":
            raise KnowledgeJobTransitionError("knowledge task is not awaiting professional review")
        report = await self.jobs.get_latest_review_report(job_id)
        if report.status != "REVIEW_REQUIRED" or not report.is_current:
            raise KnowledgeJobTransitionError(
                "knowledge report is blocked, already passed, or requires re-analysis"
            )
        if report.document_sha256 != job.content_sha256:
            raise KnowledgeJobTransitionError("review report is not bound to the staged file hash")
        requirements = review_requirements(report)
        if not requirements:
            raise KnowledgeJobTransitionError("knowledge report has no professional review scope")
        return job, report, requirements

    @staticmethod
    def _verify_staged_bytes(
        job: KnowledgeIngestionJob,
        report: KnowledgeReviewReport,
        content: bytes,
    ) -> None:
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != job.content_sha256 or actual_sha256 != report.document_sha256:
            raise KnowledgeJobTransitionError(
                "staged source hash changed after the review report was generated"
            )


def _can_review(
    identity: AgentIdentity,
    job: KnowledgeIngestionJob,
    requirement: KnowledgeReviewRequirement,
) -> bool:
    try:
        validate_reviewer(identity, job, requirement)
    except KnowledgeAdminForbidden:  # 实际提交仍返回稳定领域异常
        return False
    return True
