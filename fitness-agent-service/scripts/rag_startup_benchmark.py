"""实际RAG首次请求：懒加载对比就绪前预热；计时内没有sleep或模拟依赖。"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.model_gateway import ModelGateway
from app.infrastructure.reranker import RerankerClient
from app.rag.models import RetrievalScope
from app.rag.repository import KnowledgeRepository
from app.rag.service import RagService
from scripts.evidence_statistics import paired_change
from scripts.project_evidence_benchmark import FIXTURE, digest, isolated_database, seed


async def benchmark(pairs: int, report: dict[str, Any]) -> None:
    import torch

    torch.set_num_threads(4)
    settings = Settings()
    if settings.embedding_backend != "local" or settings.reranker_backend != "local":
        raise ValueError("startup_comparison_requires_local_models")
    report.update(
        {
            "started_at": datetime.now(UTC).isoformat(),
            "pairs": pairs,
            "fixture_sha256": digest(FIXTURE.read_bytes()),
            "torch_threads": 4,
            "production_load_evidence": False,
            "results": [],
        }
    )
    async with isolated_database(settings) as scoped:
        database = Database(scoped)
        seed_model = ModelGateway(scoped)
        try:
            await seed(KnowledgeRepository(database), seed_model)
        finally:
            await seed_model.close()
            seed_model._local_embedding = None
            del seed_model
            gc.collect()
        try:
            query = json.loads(FIXTURE.read_text())["cases"][0]["query"]
            scope = RetrievalScope(
                "evidence-user", frozenset({"evidence-org"}), frozenset({"STUDENT"})
            )
            for pair in range(pairs):
                variants = ("lazy_first_request", "warmup_before_ready")
                if pair % 2:
                    variants = tuple(reversed(variants))
                for variant in variants:
                    models, reranker = ModelGateway(scoped), RerankerClient(scoped)
                    service = RagService(
                        KnowledgeRepository(database), models, reranker, embedding_dimensions=1024
                    )
                    try:
                        ready_started = perf_counter()
                        if variant == "warmup_before_ready":
                            # 与main.lifespan一致的实际预热方法和顺序，不缓存查询答案。
                            await models.warmup_local_embedding()
                            await reranker.warmup_local_model()
                        ready_seconds = perf_counter() - ready_started
                        started = perf_counter()
                        result = await service.search(query, scope)
                        row = {
                            "pair": pair,
                            "variant": variant,
                            "ready_preparation_seconds": ready_seconds,
                            "first_rag_seconds": perf_counter() - started,
                            "ids": [chunk.id for chunk in result.chunks],
                            "relevant_hit": "warmup" in [chunk.id for chunk in result.chunks],
                        }
                        report["results"].append(row)
                        print(
                            json.dumps(
                                {
                                    "progress": "startup_pair_sample_done",
                                    "pair": pair,
                                    "variant": variant,
                                }
                            ),
                            flush=True,
                        )
                    finally:
                        await models.close()
                        models._local_embedding = None
                        reranker._local_model = None
                        del service, models, reranker
                        gc.collect()
        finally:
            await database.close()
    report["temporary_database_removed"] = True
    before = sorted(
        (r for r in report["results"] if r["variant"] == "lazy_first_request"),
        key=lambda r: r["pair"],
    )
    after = sorted(
        (r for r in report["results"] if r["variant"] == "warmup_before_ready"),
        key=lambda r: r["pair"],
    )
    report["first_request_change"] = paired_change(
        [r["first_rag_seconds"] for r in before], [r["first_rag_seconds"] for r in after]
    )
    report["startup_plus_first_request_change"] = paired_change(
        [r["first_rag_seconds"] + r["ready_preparation_seconds"] for r in before],
        [r["first_rag_seconds"] + r["ready_preparation_seconds"] for r in after],
    )
    report["completed"] = True
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["scope"] = (
        "同一进程中全新模型对象；模块和操作系统文件缓存可能已热，不是六次重启整个容器。预热把加载成本转移到ready之前，不代表稳态请求或LLM回答等比例加速。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--pairs", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        print(json.dumps({"executed": False, "error": "report_already_exists"}))
        return 1
    if not args.execute:
        print(json.dumps({"executed": False, "pairs": args.pairs, "llm_calls": 0}))
        return 0
    report: dict[str, Any] = {
        "completed": False,
        "code_sha256": digest(Path(__file__).read_bytes()),
    }
    try:
        asyncio.run(benchmark(args.pairs, report))
    except Exception as exc:  # noqa: BLE001 - 不输出连接串/正文
        report["error_type"] = type(exc).__name__
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps({"completed": report["completed"], "error_type": report.get("error_type")}))
    return 0 if report["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
