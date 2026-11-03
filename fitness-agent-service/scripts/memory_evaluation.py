"""人工标注样本检查与显式开启的真实模型类别评测；不会模拟模型。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TYPES = {
    "TRAINING_GOAL",
    "TRAINING_PREFERENCE",
    "EQUIPMENT_AVAILABILITY",
    "SCHEDULE_PREFERENCE",
    "COMMUNICATION_PREFERENCE",
}
ACTIONS = {"ADD", "UPDATE", "REVOKE", "NOOP", "NEEDS_CLARIFICATION"}


def validate(data: dict) -> None:
    ids: set[str] = set()
    for case in data["extraction"]:
        assert case["id"] not in ids and case["message"].strip()
        ids.add(case["id"])
        assert set(case["expected_types"]) <= TYPES
    for case in data["relations"]:
        assert case["id"] not in ids and case["old"].strip() and case["message"].strip()
        ids.add(case["id"])
        assert case["expected_action"] in ACTIONS


async def evaluate_live(data: dict) -> dict:
    from app.core.config import Settings
    from app.infrastructure.model_gateway import ModelGateway
    from app.memory.candidate import MemoryCandidateExtractionService

    settings = Settings()
    if not settings.llm_configured:
        raise RuntimeError("未配置真实模型；没有执行模型评测")

    class CountedGateway:
        def __init__(self) -> None:
            self.gateway = ModelGateway(settings)
            self.attempted = 0
            self.succeeded = 0

        async def chat_json(self, *args, **kwargs):
            self.attempted += 1
            value = await self.gateway.chat_json(*args, **kwargs)
            self.succeeded += 1
            return value

    gateway = CountedGateway()
    extractor = MemoryCandidateExtractionService(gateway)  # type: ignore[arg-type]
    rows = []
    tp = fp = fn = errors = 0
    for case in data["extraction"]:
        expected = set(case["expected_types"])
        calls_before = gateway.attempted
        try:
            candidates = await extractor.propose_controlled(case["message"])
            actual = {candidate.memory_type for candidate in candidates}
            tp += len(expected & actual)
            fp += len(actual - expected)
            fn += len(expected - actual)
            rows.append(
                {
                    "id": case["id"],
                    "expected": sorted(expected),
                    "actual": sorted(actual),
                    "pass": actual == expected,
                    "model_called": gateway.attempted > calls_before,
                }
            )
        except Exception as exc:  # noqa: BLE001 - report failures, never treat them as empty output
            errors += 1
            rows.append(
                {
                    "id": case["id"],
                    "error_type": type(exc).__name__,
                    "pass": False,
                    "model_called": gateway.attempted > calls_before,
                }
            )
    scored = len(rows) - errors
    return {
        "mode": "real_model",
        "model": settings.llm_model,
        "total": len(rows),
        "scored": scored,
        "errors": errors,
        "model_attempts": gateway.attempted,
        "model_successes": gateway.succeeded,
        "cases_skipped_before_model": sum(not row["model_called"] for row in rows),
        "pipeline_exact_case_accuracy_including_errors": sum(row["pass"] for row in rows)
        / len(rows)
        if rows and scored
        else None,
        "type_precision_scored": tp / (tp + fp) if tp + fp else None,
        "type_recall_scored": tp / (tp + fn) if tp + fn else None,
        "scope": "评测受控提取流水线（model_called=false 表示未调用），不是模型单独准确率；仅比较类别集合，不验证提取值、数量、证据、冲突或安全性。",
        "relation_cases_evaluated": 0,
        "results": rows,
    }


async def evaluate_relations(data: dict, *, live: bool = False) -> dict:
    """运行真实比较器；仅显式 live 模式调用模型，错误不计为通过。"""
    from datetime import UTC, datetime

    from app.memory.candidate import MemoryCandidate
    from app.memory.models import FitnessMemory
    from app.memory.reconciliation import reconcile_candidate

    class ModelRequired(RuntimeError):
        pass

    class NoModel:
        async def chat_json(self, *args, **kwargs):
            raise ModelRequired()

    class CountedGateway:
        def __init__(self, gateway):
            self.gateway = gateway
            self.attempted = 0
            self.succeeded = 0

        async def chat_json(self, *args, **kwargs):
            self.attempted += 1
            result = await self.gateway.chat_json(*args, **kwargs)
            self.succeeded += 1
            return result

    if live:
        from app.core.config import Settings
        from app.infrastructure.model_gateway import ModelGateway

        settings = Settings()
        if not settings.llm_configured:
            raise RuntimeError("未配置真实模型；没有执行模型比较评测")
        models = CountedGateway(ModelGateway(settings))
    else:
        models = NoModel()

    rows = []
    now = datetime.now(UTC)
    for case in data["relations"]:
        if case["expected_action"] == "REVOKE":
            rows.append({"id": case["id"], "status": "unsupported_revoke"})
            continue
        previous = []
        if case["old_present"]:
            previous.append(
                FitnessMemory(
                    id="fixture-old",
                    subject_user_id="fixture-user",
                    organization_id="fixture-org",
                    memory_type=case["memory_type"],
                    memory_key=case["old_slot"],
                    slot_code=case["old_slot"],
                    content={"value": case["old_value"]},
                    source_type="USER_EXPLICIT",
                    confidence=1,
                    status="ACTIVE",
                    version=1,
                    expires_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        candidate = MemoryCandidate(
            memory_type=case["memory_type"],
            memory_key=case["slot"],
            slot_code=case["slot"],
            value=case["new_value"],
            evidence_quote=case["message"],
        )
        try:
            result = await reconcile_candidate(candidate, previous, models)
        except ModelRequired:
            rows.append({"id": case["id"], "status": "requires_model"})
            continue
        except Exception as exc:  # noqa: BLE001 - preserve failed cases in evaluation
            rows.append({"id": case["id"], "status": "error", "error_type": type(exc).__name__})
            continue
        rows.append(
            {
                "id": case["id"],
                "status": "scored",
                "expected": case["expected_action"],
                "actual": result.action,
                "pass": result.action == case["expected_action"],
            }
        )
    scored = [row for row in rows if row["status"] == "scored"]
    return {
        "mode": "relation_live" if live else "relation_rules_only",
        "total": len(rows),
        "scored": len(scored),
        "passed": sum(row["pass"] for row in scored),
        "requires_model": sum(row["status"] == "requires_model" for row in rows),
        "unsupported_revoke": sum(row["status"] == "unsupported_revoke" for row in rows),
        "errors": sum(row["status"] == "error" for row in rows),
        "model_attempts": models.attempted if live else 0,
        "model_successes": models.succeeded if live else 0,
        "note": "调用真实比较器；需要模型、尚不支持和错误的样本不计作通过。",
        "results": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals/memory_labeled.json")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--live", action="store_true", help="调用已配置的真实模型，有网络和费用")
    modes.add_argument("--relation-rules", action="store_true", help="只运行无需模型的比较规则")
    modes.add_argument("--relation-live", action="store_true", help="调用真实模型评测记忆比较")
    parser.add_argument("--output", type=Path, help="输出报告文件；默认 stdout")
    args = parser.parse_args()
    data = json.loads(args.dataset.read_text())
    validate(data)
    report = (
        asyncio.run(evaluate_live(data))
        if args.live
        else asyncio.run(evaluate_relations(data, live=args.relation_live))
        if args.relation_rules or args.relation_live
        else {
            "mode": "dataset_validation_only",
            "extraction_cases": len(data["extraction"]),
            "relation_cases": len(data["relations"]),
            "model_calls": 0,
            "note": "只检查标注格式，不是提取或比较准确率评测。真实模型请显式传入 --live；关系样本尚未执行。",
        }
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload + "\n")
    else:
        print(payload)
    if args.live and report["errors"]:
        return 2
    if (args.relation_rules or args.relation_live) and (
        report["errors"] or report["passed"] != report["scored"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
