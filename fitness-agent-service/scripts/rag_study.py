"""运行 python -m scripts.rag_study --help；默认不连接数据库、不调用模型。"""

from __future__ import annotations

import argparse
import asyncio
import copy
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.rag.study.contracts import Protocol, Question, now, read, save, unique, unseal
from app.rag.study.reporting import markdown, summarize
from app.rag.study.runtime import StudyRuntime
from app.rag.study.synthetic import generate
from app.rag.study.workflow import freeze, prepare, reconcile, review_packet


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x", encoding="utf-8") as output:
        path.chmod(0o600)
        output.write(value)


def initialize(directory: Path, source: str, study_id: str) -> None:
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("研究目录必须为空，避免覆盖证据")
    if source == "synthetic":
        values = generate(study_id)
    else:
        protocol = Protocol.model_validate(
            {
                "study_id": study_id,
                "source_kind": source,
                "corpus_origin": "authorized_business_documents",
                "purpose": "经过同意的真实试用问题采集与独立RAG召回研究",
                "created_at": now(),
            }
        )
        values = {"protocol": protocol.model_dump(), "questions": [], "triage": [], "scopes": []}
    for name, value in values.items():
        save(directory / f"{name}.json", value)


def prepare_directory(directory: Path) -> dict[str, Any]:
    result = prepare(
        *(
            read(directory / f"{name}.json")
            for name in ("protocol", "questions", "scopes", "triage")
        )
    )
    save(directory / "prepared.json", result)
    return result


def import_questions(directory: Path, inputs: list[Path]) -> None:
    """合并采集文件到新工件；初始空模板与原始来源文件均不覆盖。"""
    protocol = Protocol.model_validate(read(directory / "protocol.json"))
    questions = [Question.model_validate(row) for path in inputs for row in read(path)]
    unique(questions, "case_id")
    if not questions or any(
        q.source_kind != protocol.source_kind or q.consent_version != protocol.consent_version
        for q in questions
    ):
        raise ValueError("空记录或问题来源/同意版本不匹配")
    save(directory / "questions-imported.json", [q.model_dump() for q in questions])
    save(
        directory / "triage-draft.json",
        [
            {
                "case_id": q.case_id,
                "retrieval_query": q.query,
                "topic": "待分类",
                "family_id": "",
                "redaction_reviewed": False,
                "source_reviewed": False,
                "reviewed_by": "",
                "context_resolution_note": "",
            }
            for q in questions
        ],
    )
    save(
        directory / "scopes-draft.json",
        [
            {
                "participant_id": participant,
                "subject": "",
                "organization_ids": [],
                "roles": [],
                "verified_by": "",
                "verification_reference": "",
            }
            for participant in sorted({q.participant_id for q in questions})
        ],
    )


def export_report(directory: Path, split: str) -> dict[str, Any]:
    summary = summarize(read(directory / f"run-{split}.json"))
    save(directory / f"summary-{split}.json", summary)
    write_text(directory / f"report-{split}.md", markdown(summary))
    return summary


async def demo(directory: Path) -> dict[str, Any]:
    from app.infrastructure.database import Database
    from app.rag.repository import KnowledgeRepository
    from scripts.project_evidence_benchmark import isolated_database, seed

    settings = Settings()
    if settings.embedding_backend != "local" or settings.reranker_backend != "local":
        raise ValueError("demo只允许本地模型，避免不必要的第三方数据传输或费用")
    import torch

    torch.set_num_threads(4)
    initialize(directory, "synthetic", directory.name)
    prepared = prepare_directory(directory)
    async with isolated_database(settings) as scoped:
        runtime = StudyRuntime(scoped)
        database = Database(scoped)
        try:
            print("正在临时数据库中建立合成语料并运行本地检索…", flush=True)
            await seed(KnowledgeRepository(database), runtime.models)
            pool = await runtime.build_pool(prepared)
            save(directory / "pool.json", pool)
            labels = read(directory / "fixture_labels.json")
            a = {
                "pool_sha256": pool["sha256"],
                "reviewer_id": "fixture-A",
                "reviewer_kind": "scripted_fixture",
                "independent_review_declared": True,
                "labels": labels,
            }
            b = copy.deepcopy(a)
            b["reviewer_id"] = "fixture-B"
            b["labels"][0].update(category="needs_context", relevant_ids=[])
            for name, review in (("review-a", a), ("review-b", b)):
                save(directory / f"{name}.json", review)
            reconciled = reconcile(pool, a, b)
            save(directory / "reconciliation.json", reconciled)
            adjudicated = {**a, "reviewer_id": "fixture-C", "labels": [labels[0]]}
            save(directory / "adjudication.json", adjudicated)
            frozen = freeze(pool, reconciled, adjudicated)
            save(directory / "frozen.json", frozen)
            save(directory / "annotation-demo.json", review_packet(pool, "human-preview", "human"))
            print("候选池、模拟独立评审和分歧裁决完成，正在运行冻结测试集…", flush=True)
            for split in ("dev", "test"):
                save(directory / f"run-{split}.json", await runtime.evaluate(frozen, split))
                export_report(directory, split)
            return read(directory / "summary-test.json")
        finally:
            await database.close()
            await runtime.close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in (
        "init",
        "prepare",
        "pool",
        "packet",
        "reconcile",
        "freeze",
        "evaluate",
        "report",
        "demo",
        "import",
    ):
        item = commands.add_parser(name)
        item.add_argument("directory", type=Path, help="建议var/rag-studies/<研究名>，不要放进Git")
        if name == "init":
            item.add_argument(
                "--source", choices=("synthetic", "pilot", "production_log"), default="pilot"
            )
        if name == "import":
            item.add_argument("--input", type=Path, nargs="+", required=True)
        if name == "prepare":
            item.add_argument(
                "--imported",
                action="store_true",
                help="使用负责人已复核的导入问题及triage/scopes-draft",
            )
        if name in {"pool", "evaluate", "demo"}:
            item.add_argument(
                "--execute", action="store_true", help="允许数据库和模型执行；远端模型可能计费"
            )
        if name in {"evaluate", "report"}:
            item.add_argument("--split", choices=("dev", "test"), default="test")
        if name == "packet":
            item.add_argument("--reviewer", required=True)
            item.add_argument("--output", type=Path, required=True)
            item.add_argument("--conflicts-only", action="store_true")
    return result


async def run(args: argparse.Namespace) -> None:
    directory = args.directory.resolve()
    command = args.command
    if command == "init":
        initialize(directory, args.source, directory.name)
    elif command == "import":
        import_questions(directory, args.input)
    elif command == "prepare":
        if args.imported:
            save(
                directory / "prepared.json",
                prepare(
                    *(
                        read(directory / f"{name}.json")
                        for name in (
                            "protocol",
                            "questions-imported",
                            "scopes-draft",
                            "triage-draft",
                        )
                    )
                ),
            )
        else:
            prepare_directory(directory)
    elif command == "packet":
        packet = review_packet(read(directory / "pool.json"), args.reviewer, "human")
        if args.conflicts_only:
            conflicts = unseal(read(directory / "reconciliation.json"), "reconciliation")[
                "conflicts"
            ]
            ids = {row["case_id"] for row in conflicts}
            packet["cases"] = [row for row in packet["cases"] if row["case_id"] in ids]
            packet["conflicts"] = conflicts
        save(args.output, packet)
    elif command == "reconcile":
        save(
            directory / "reconciliation.json",
            reconcile(
                *(read(directory / f"{name}.json") for name in ("pool", "review-a", "review-b"))
            ),
        )
    elif command == "freeze":
        adjudication = directory / "adjudication.json"
        save(
            directory / "frozen.json",
            freeze(
                read(directory / "pool.json"),
                read(directory / "reconciliation.json"),
                read(adjudication) if adjudication.exists() else None,
            ),
        )
    elif command == "report":
        export_report(directory, args.split)
    elif command in {"pool", "evaluate", "demo"}:
        if not args.execute:
            print("预检：尚未连接数据库/调用模型。核验权限、目录及模型配置后加 --execute。")
            return
        if command == "demo":
            summary = await demo(directory)
            print(
                f"合成演示完成：测试集{summary['case_count']}条；失败{summary['failure_count']}条。"
            )
        else:
            runtime = StudyRuntime(Settings())
            try:
                if command == "pool":
                    save(
                        directory / "pool.json",
                        await runtime.build_pool(read(directory / "prepared.json")),
                    )
                else:
                    save(
                        directory / f"run-{args.split}.json",
                        await runtime.evaluate(read(directory / "frozen.json"), args.split),
                    )
            finally:
                await runtime.close()
    print(f"完成 {command}；资料目录：{directory}")


if __name__ == "__main__":
    asyncio.run(run(parser().parse_args()))
