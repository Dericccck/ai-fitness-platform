"""只用合成 RAG 资料测量真实流式 TTFT/总耗时/缓存命中，不执行业务工具。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.core.config import Settings
from app.infrastructure.model_gateway import (
    ModelConfigurationError,
    ModelGateway,
    ModelResponseError,
)
from app.infrastructure.model_usage import collect_usage
from app.streaming.events import stream_events


async def benchmark(samples: int) -> dict[str, Any]:
    settings = Settings()
    model = ModelGateway(settings)
    results: list[dict[str, Any]] = []
    fixture = "合成健身资料：训练前从低强度热身开始，疲劳时调整强度，出现不适停止训练。\n" * 100
    try:
        for index in range(samples):
            # 交替配对，不强行宣称某一轮是供应商冷缓存，也不删除供应商缓存。
            for variant in ("stable", "variable") if index % 2 == 0 else ("variable", "stable"):
                system = "仅根据以下合成资料简短回答，不诊断、不编造事实。\n" + fixture
                if variant == "variable":
                    system = "合成基准编号：" + uuid4().hex + "\n" + system
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": "用两句话说明怎样安全热身"},
                ]

                async def generate(state: dict[str, Any]) -> dict[str, Any]:
                    await model.chat_with_tools(
                        state["messages"], tools=[], temperature=0, max_output_tokens=128
                    )
                    return {"done": True}

                graph = StateGraph(dict)
                graph.add_node("generate", generate)
                graph.add_edge(START, "generate")
                graph.add_edge("generate", END)
                started = perf_counter()
                first: float | None = None
                with collect_usage(f"benchmark:{variant}:{index}") as usage, stream_events():
                    async for mode, chunk in graph.compile().astream(
                        {"messages": messages}, stream_mode=["custom", "values"]
                    ):
                        if (
                            mode == "custom"
                            and chunk.get("event") == "answer.delta"
                            and first is None
                        ):
                            first = perf_counter() - started
                results.append(
                    {
                        "variant": variant,
                        "sample": index,
                        "ttft_seconds": first,
                        "total_seconds": perf_counter() - started,
                        "usage": usage.summary(),
                        "cache_hit_tokens": sum(c.cache_hit_tokens or 0 for c in usage.calls),
                        "cache_usage_known": all(
                            c.cache_hit_tokens is not None for c in usage.calls
                        ),
                    }
                )
        return {
            "passed": True,
            "mode": "live_synthetic_stream_benchmark",
            "model": settings.llm_model,
            "samples_per_variant": samples,
            "results": results,
            "production_load_evidence": False,
            "interpretation": "小样本配对观测；不代表上线规模、P95 SLA或确定的缓存加速比例",
        }
    finally:
        await model.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="明确启用真实计费模型调用")
    parser.add_argument("--samples", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {"mode": "preflight", "model_calls_planned": 2 * args.samples, "executed": False}
            )
        )
        return 0
    try:
        report = asyncio.run(benchmark(args.samples))
    except (ModelConfigurationError, ModelResponseError, ValueError) as exc:
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}))
        return 1
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
