"""从当前 Agent 运行时代码生成 v2 Release Manifest 组件摘要输入。

Prompt 和 Tool Schema 必须来自实际运行时代码，不能另维护一份容易漂移的快照。
模型制品仍由构建/制品流水线显式提供；本脚本不会下载、复制或伪造模型权重摘要。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from typing import Any

from app.agent.domain_subgraphs import executable_domain_specs
from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import _system_prompt
from app.core.config import Settings
from app.infrastructure.gateway_client import GatewayClient
from scripts.build_release_components import build_components
from scripts.release_manifest_check import ReleaseManifestError


def _safe_config(settings: Settings) -> dict[str, Any]:
    """提取可公开记录的有效配置，不读出 Secret、路径或连接地址。"""

    return {
        "llm_model": settings.llm_model,
        # 这里使用发布清单的规范字段名，避免把非敏感的输出预算误判为凭证字段；
        # 任何真实 token/secret/password/api_key 配置仍由 build_components 拒绝。
        "llm_output_limit": settings.llm_max_output_tokens,
        "training_plan_output_limit": settings.training_plan_max_output_tokens,
        "agent_max_tool_steps": settings.agent_max_tool_steps,
        "embedding_backend": settings.embedding_backend,
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "reranker_backend": settings.reranker_backend,
        "reranker_model": settings.reranker_model,
        "rag_candidate_limit": settings.rag_candidate_limit,
        "rag_keyword_candidate_limit": settings.rag_keyword_candidate_limit,
        "rag_top_k": settings.rag_top_k,
        "rag_prompt_max_total_chars": settings.rag_prompt_max_total_chars,
        "rag_prompt_max_evidence_chars": settings.rag_prompt_max_evidence_chars,
        "rag_embedding_batch_size": settings.rag_embedding_batch_size,
        "rag_chunk_max_chars": settings.rag_chunk_max_chars,
        "rag_chunk_overlap_chars": settings.rag_chunk_overlap_chars,
        "rag_vector_weight": settings.rag_vector_weight,
        "rag_keyword_weight": settings.rag_keyword_weight,
        "rag_rrf_k": settings.rag_rrf_k,
    }


def _schema_filename(tool_id: str) -> str:
    """用稳定摘要命名临时文件，避免工具 ID 中的点号造成路径歧义。"""

    digest = hashlib.sha256(tool_id.encode("utf-8")).hexdigest()[:16]
    return f"{digest}.json"


async def _write_runtime_sources(root: Path, settings: Settings) -> tuple[list[str], list[str]]:
    """导出真实 Prompt/Tool Schema，并返回 generator 所需的 NAME=PATH 参数。"""

    prompts_dir = root / "prompts"
    schemas_dir = root / "tool-schemas"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    schemas_dir.mkdir(parents=True, exist_ok=True)

    prompt_args: list[str] = []
    for spec in executable_domain_specs():
        path = prompts_dir / f"{spec.node_name}.txt"
        path.write_text(_system_prompt(spec.route, "zh-CN"), encoding="utf-8")
        prompt_args.append(f"{spec.node_name}={path}")

    gateway = GatewayClient(settings)
    try:
        registry = build_fitness_tool_registry(gateway)
        schema_args: list[str] = []
        for schema in registry.public_specs():
            tool_id = str(schema["name"])
            path = schemas_dir / _schema_filename(tool_id)
            path.write_text(
                json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            schema_args.append(f"{tool_id}={path}")
        return prompt_args, schema_args
    finally:
        await gateway.close()


async def _build(args: Namespace) -> dict[str, Any]:
    settings = Settings()
    with tempfile.TemporaryDirectory(prefix="agent-release-sources-") as temp_dir:
        prompt_args, schema_args = await _write_runtime_sources(Path(temp_dir), settings)
        component_args = Namespace(
            eval_release=args.eval_release,
            prompt=prompt_args,
            model_artifact=args.model_artifact,
            tool_schema=schema_args,
            safe_config=[
                f"{key}={json.dumps(value, ensure_ascii=False)}"
                for key, value in _safe_config(settings).items()
            ],
            index_build_id=args.index_build_id,
        )
        return build_components(component_args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="组件 JSON 输出路径")
    parser.add_argument("--eval-release", type=Path, required=True, help="正式评测发布描述")
    parser.add_argument(
        "--model-artifact",
        action="append",
        required=True,
        help="真实模型制品：NAME=PATH；至少提供一个，不允许伪造摘要",
    )
    parser.add_argument(
        "--index-build-id",
        required=True,
        help="已完成且可查询的索引构建 ID；运行时代码路径不提供默认值",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        payload = asyncio.run(_build(args))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"运行时代码 v2 组件摘要已生成：{args.output}")
        return 0
    except (OSError, ReleaseManifestError, ValueError) as exc:
        print(f"运行时代码 v2 组件摘要生成失败：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
