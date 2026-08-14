"""健身 Agent 的长期 Memory 领域能力。

Memory 只保存用户明确表达、且对后续训练计划有长期价值的结构化信息；它不是完整
聊天记录，也不是医疗档案。写入必须经过确认，读取必须使用签名身份做主体和机构隔离。
"""

from .candidate import (
    MemoryCandidate,
    MemoryCandidateExtractionError,
    MemoryCandidateExtractionService,
    MemoryCandidateRecord,
    build_candidate_context,
)
from .candidate_repository import (
    MemoryCandidateNotFound,
    MemoryCandidateRepository,
    MemoryCandidateStateError,
)
from .candidate_service import (
    MemoryCandidateDecisionResult,
    MemoryCandidatePersistenceError,
    MemoryCandidateService,
)
from .models import FitnessMemory, MemoryType, MemoryValidationError
from .service import MemoryService

__all__ = [
    "FitnessMemory",
    "MemoryCandidate",
    "MemoryCandidateDecisionResult",
    "MemoryCandidateExtractionError",
    "MemoryCandidateExtractionService",
    "MemoryCandidateNotFound",
    "MemoryCandidatePersistenceError",
    "MemoryCandidateRecord",
    "MemoryCandidateRepository",
    "MemoryCandidateService",
    "MemoryCandidateStateError",
    "MemoryService",
    "MemoryType",
    "MemoryValidationError",
    "build_candidate_context",
]
