"""Fixed synthetic KNOW-001 observation with isolated pgvector and local RAG models.

Run only via task_eval_rag_real.sh. This is a draft observation, not an accepted task.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy.engine import make_url

from app.agent.supervisor import Supervisor, SupervisorRequest, _system_prompt
from app.agent.tool_registry import ToolRegistry
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.infrastructure.gateway_client import GatewayRequestContext
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.reranker import RerankerClient
from app.rag.models import KnowledgeChunkInput, KnowledgeDocumentInput, RetrievalScope
from app.rag.repository import KnowledgeRepository
from app.rag.service import RagService
from scripts.project_evidence_benchmark import isolated_database

from .budget_store import DurableBudget
from .dev_review_probe import _Gateway, _read_credential, _sanitized, _sha, _write_private
from .supervisor_model_probe import SYSTEM_PROMPT

QUERY = "深蹲上推时怎么呼吸？"
DATASET_SHA = "1d187f5abca4dab754db29059f9ff95a7897719fde6ce88de6c92448d25ab446"
ROOT = Path(__file__).resolve().parents[3]
DOCUMENTS = (
    ("eval-breathing", "eval-org-a", "训练计划示例：深蹲上推阶段呼气，下蹲阶段吸气。"),
    ("eval-decoy", "eval-org-a", "训练计划示例：合理安排休息和训练时间。"),
    ("eval-foreign", "eval-org-b", "深蹲上推时怎么呼吸？其他机构专用的合成材料。"),
)


def _docker_output(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True, timeout=10
    ).stdout.strip()


def validate_isolated_environment(environ: Any = os.environ) -> None:
    """Fail closed unless a freshly labelled isolated container owns the DB port."""
    run_id = environ.get("TASK_EVAL_RAG_RUN_ID", "")
    pg_id = environ.get("TASK_EVAL_RAG_PG_ID", "")
    network_id = environ.get("TASK_EVAL_RAG_NETWORK_ID", "")
    port = environ.get("TASK_EVAL_RAG_PG_PORT", "")
    if not (
        re.fullmatch(r"[0-9a-f]{16}", run_id)
        and re.fullmatch(r"[0-9a-f]{64}", pg_id)
        and re.fullmatch(r"[0-9a-f]{64}", network_id)
        and re.fullmatch(r"[0-9]{1,5}", port)
        and 1 <= int(port) <= 65535
    ):
        raise ValueError("invalid isolated container identity")
    url = make_url(environ.get("AGENT_DATABASE_URL", ""))
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != "127.0.0.1"
        or url.port != int(port)
        or url.database != "postgres"
        or url.username != "fitness_eval"
        or not url.password
        or url.password != environ.get("POSTGRES_PASSWORD")
    ):
        raise ValueError("invalid isolated database target")
    container = _docker_output(
        "inspect",
        "--format",
        '{{index .Config.Labels "fitness.eval.rag"}}|{{.Config.Image}}|{{.HostConfig.NetworkMode}}|{{.State.Running}}',
        pg_id,
    )
    if container != f"{run_id}|pgvector/pgvector:pg16|{network_id}|true":
        # Docker may normalize network mode to a name or ID. The launcher passes
        # the network ID and Docker uses that exact supplied value.
        raise ValueError("isolated container contract mismatch")
    mounts = json.loads(_docker_output("inspect", "--format", "{{json .HostConfig.Tmpfs}}", pg_id))
    if not isinstance(mounts, dict) or "/var/lib/postgresql/data" not in mounts:
        raise ValueError("postgres data is not ephemeral")
    network = _docker_output(
        "network",
        "inspect",
        "--format",
        '{{.Internal}}|{{index .Labels "fitness.eval.rag"}}',
        network_id,
    )
    if network != f"false|{run_id}":
        raise ValueError("network is not isolated")
    members = json.loads(
        _docker_output("network", "inspect", "--format", "{{json .Containers}}", network_id)
    )
    if not isinstance(members, dict) or set(members) != {pg_id}:
        raise ValueError("network is not isolated")
    if _docker_output("port", pg_id, "5432/tcp") != f"127.0.0.1:{port}":
        raise ValueError("database port binding mismatch")


def validate_models(settings: Settings) -> None:
    if settings.embedding_backend != "local" or settings.reranker_backend != "local":
        raise ValueError("only local embedding and reranker are allowed")
    for value in (settings.embedding_model_path, settings.reranker_model_path):
        path = Path(value)
        if not path.is_dir() or not (path / "config.json").is_file():
            raise ValueError("local model files unavailable")


async def seed(repository: KnowledgeRepository, models: ModelGateway) -> None:
    embeddings = await models.embed([text for _, _, text in DOCUMENTS])
    if len(embeddings) != len(DOCUMENTS):
        raise ValueError("incomplete fixture embeddings")
    date = datetime(2025, 1, 1, tzinfo=UTC)
    for (identifier, organization, content), embedding in zip(DOCUMENTS, embeddings, strict=True):
        checksum = hashlib.sha256(content.encode()).hexdigest()
        document = KnowledgeDocumentInput(
            identifier,
            organization,
            identifier,
            f"synthetic://eval/{identifier}",
            "FITNESS_GUIDE",
            "ORGANIZATION",
            ("STUDENT",),
            1,
            "PUBLISHED",
            checksum,
            date,
            None,
        )
        chunk = KnowledgeChunkInput(
            identifier,
            identifier,
            0,
            content,
            checksum,
            organization,
            None,
            "ORGANIZATION",
            ("STUDENT",),
            "FITNESS_GUIDE",
            date,
            None,
            {},
        )
        await repository.replace_document(document, [chunk], [embedding])


async def observe(credential: str, budget: DurableBudget, review_dir: Path) -> dict[str, Any]:
    """Search real scoped SQL and invoke the real Supervisor exactly once."""
    if (
        review_dir.is_symlink()
        or not review_dir.is_dir()
        or review_dir.stat().st_uid != os.geteuid()
        or stat.S_IMODE(review_dir.stat().st_mode) != 0o700
    ):
        raise ValueError("private review directory unavailable")
    settings = Settings()
    validate_models(settings)
    if _system_prompt("FITNESS_COACHING", "zh-CN", intent="FITNESS_KNOWLEDGE_QA") != SYSTEM_PROMPT:
        raise ValueError("model prompt changed")
    case_path = ROOT / "evals/task_eval/datasets/v1.jsonl"
    if hashlib.sha256(case_path.read_bytes()).hexdigest() != DATASET_SHA:
        raise ValueError("frozen dataset changed")
    cases = [json.loads(line) for line in case_path.read_text().splitlines()]
    matching = [case for case in cases if case["case_id"] == "KNOW-001"]
    if len(matching) != 1 or matching[0]["steps"][0].get("message") != QUERY:
        raise ValueError("frozen case changed")
    async with isolated_database(settings) as scoped:
        db = Database(scoped)
        embeddings = ModelGateway(scoped)
        try:
            repository = KnowledgeRepository(db)
            await seed(repository, embeddings)
            service = RagService(
                repository,
                embeddings,
                RerankerClient(scoped),
                top_k=2,
                embedding_dimensions=scoped.embedding_dimensions,
            )
            identity = AgentIdentity(
                "eval-user", frozenset({"eval-org-a"}), frozenset({"STUDENT"}), 1, 9999999999
            )
            scope = RetrievalScope(identity.subject, identity.organization_ids, identity.roles)
            result = await service.search(QUERY, scope)
            ids = [chunk.id for chunk in result.chunks]
            if "eval-breathing" not in ids or "eval-foreign" in ids:
                raise ValueError("retrieval or scope check failed")
            # Positive control proves the foreign fixture exists and its ACL is
            # accessible from its own authorized scope, before the model call.
            foreign = await repository.search_candidates(
                (await embeddings.embed([QUERY]))[0],
                RetrievalScope("foreign-user", frozenset({"eval-org-b"}), frozenset({"STUDENT"})),
                limit=5,
            )
            if "eval-foreign" not in [chunk.id for chunk in foreign]:
                raise ValueError("foreign ACL positive control failed")
            gateway = _Gateway(QUERY, credential, budget, None)
            gateway.messages[0]["content"] = SYSTEM_PROMPT + "\n\n" + result.as_prompt_context()
            supervisor = Supervisor(
                cast(ModelGateway, gateway), ToolRegistry(), rag_service=service
            )
            response = await supervisor.invoke(
                SupervisorRequest(
                    user_message=QUERY,
                    gateway_context=GatewayRequestContext("synthetic-context"),
                    conversation_id="synthetic-rag-know-001",
                    identity=identity,
                )
            )
            if gateway.reason != "MODEL_RESPONSE_RECEIVED" or gateway.attempts != 1:
                raise ValueError("model did not provide a complete answer")
            if (
                list(response.retrieved_evidence_ids) != ids
                or list(response.knowledge_citation_evidence_ids) != ids
            ):
                raise ValueError("supervisor retrieval changed between reads")
            answer = _sanitized(response.answer, credential)
            citation_indices = [
                int(number) for number in re.findall(r"\[证据([1-9][0-9]{0,2})\]", answer)
            ]
            if any(index > len(ids) for index in citation_indices):
                raise ValueError("citation binding invalid")
            review_file = _write_private(
                review_dir,
                {
                    "case_id": "KNOW-001",
                    "review_status": "NOT_REVIEWED",
                    "answer": answer,
                    "warning": "Unreviewed model output; synthetic evidence is not clinical guidance.",
                },
            )
            return {
                "case_id": "KNOW-001",
                "scope": "isolated_synthetic_dev_observation",
                "component_status": "OBSERVED",
                "task_status": "UNKNOWN",
                "human_review": "NOT_REVIEWED",
                "dataset_sha256": DATASET_SHA,
                "retrieved_evidence_ids": ids,
                "foreign_positive_control": True,
                "foreign_id_returned_to_subject": False,
                "retrieval_origin": "postgres_pgvector_real_repository_local_embedding_local_reranker",
                "answer_sha256": _sha(answer),
                "model_output_sha256": gateway.output_hash,
                "model_attempts": gateway.attempts,
                "model_input_tokens": gateway.usage[0],
                "model_output_tokens": gateway.usage[1],
                "model_input_sha256": _sha(
                    json.dumps(gateway.messages, ensure_ascii=False, separators=(",", ":"))
                ),
                "review_file": review_file,
                "citation_binding": "MATCHED" if citation_indices else "NO_VALID_CITATION",
                "citation_indices": citation_indices,
                "citation_evidence_ids": [ids[index - 1] for index in citation_indices],
                "answer_citation_support": "NOT_VERIFIED",
                "provider_bill_verified": False,
                "local_budget": budget.snapshot(),
            }
        finally:
            await embeddings.close()
            await db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for key in (
        "credential-file",
        "budget-file",
        "budget-limit-nano",
        "budget-opening-nano",
        "minimum-reserved-nano",
        "review-directory",
        "report-file",
    ):
        parser.add_argument("--" + key, required=True)
    parser.add_argument("--execute", required=True, action="store_true")
    parser.add_argument("--confirm-current-pricing", required=True, action="store_true")
    args = parser.parse_args(argv)
    try:
        output_fd = os.open(
            args.report_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    except OSError:
        print(json.dumps({"status": "ERROR", "reason": "REPORT_RESERVATION_FAILED"}))
        return 1
    report: dict[str, Any] = {"status": "ERROR", "reason": "PRECHECK_OR_RUN_FAILED"}
    try:
        validate_isolated_environment()
        minimum = int(args.minimum_reserved_nano)
        budget = DurableBudget.open_existing(
            args.budget_file,
            limit_nano=int(args.budget_limit_nano),
            opening_nano=int(args.budget_opening_nano),
        )
        snapshot = budget.snapshot()
        if (
            minimum < 0
            or snapshot["frozen"]
            or snapshot["pending"]
            or snapshot["reserved_nano"] < minimum
        ):
            raise ValueError("budget history unavailable")
        credential = _read_credential(Path(args.credential_file))
        report = asyncio.run(observe(credential, budget, Path(args.review_directory)))
    except Exception as exc:  # noqa: BLE001 - never expose provider/database exception details
        safe_reasons = {
            "invalid isolated container identity",
            "invalid isolated database target",
            "isolated container contract mismatch",
            "postgres data is not ephemeral",
            "network is not isolated",
            "database port binding mismatch",
            "only local embedding and reranker are allowed",
            "local model files unavailable",
            "private review directory unavailable",
            "model prompt changed",
            "frozen dataset changed",
            "frozen case changed",
            "retrieval or scope check failed",
            "foreign ACL positive control failed",
            "model did not provide a complete answer",
            "supervisor retrieval changed between reads",
            "citation binding invalid",
            "budget history unavailable",
            "isolated_migration_failed",
        }
        message = str(exc)
        report = {
            "status": "ERROR",
            "reason": message if message in safe_reasons else "PRECHECK_OR_RUN_FAILED",
        }
    with os.fdopen(output_fd, "w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "case_id",
                    "component_status",
                    "task_status",
                    "reason",
                    "model_attempts",
                )
                if key in report
            },
            ensure_ascii=False,
        )
    )
    return 3 if report.get("component_status") == "OBSERVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
