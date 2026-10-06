"""从实际 Git 历史提取纯 Prompt 文本；固定当前模型、工具 Schema 和输入协议。"""

from __future__ import annotations

import argparse
import ast
import asyncio
import copy
import json
import subprocess
from pathlib import Path
from typing import Any

from app.agent.domain_subgraphs import domain_agent_spec
from app.core.config import Settings
from app.evaluation.prompt_regression import runtime_snapshot, validate_dataset

SOURCE = "fitness-agent-service/app/agent/supervisor.py"


def historical_function(source: str) -> Any:
    functions = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_system_prompt"
    ]
    if len(functions) != 1:
        raise ValueError("历史版本没有唯一 Prompt 构造函数")
    function = functions[0]
    # 只运行已检查的纯字符串函数，不导入历史整个应用，也不执行历史启动钩子。
    for node in ast.walk(function):
        if isinstance(
            node,
            (
                ast.Import,
                ast.ImportFrom,
                ast.While,
                ast.For,
                ast.With,
                ast.Try,
                ast.Lambda,
                ast.NamedExpr,
            ),
        ):
            raise TypeError("历史构造器不符合纯字符串契约")
        if isinstance(node, ast.Call) and not (
            isinstance(node.func, ast.Name) and node.func.id == "domain_agent_spec"
        ):
            raise ValueError("历史构造器含非受控调用")
        if isinstance(node, ast.Attribute) and not (
            isinstance(node.value, ast.Name)
            and node.value.id == "spec"
            and node.attr == "display_name"
        ):
            raise ValueError("历史构造器含非受控属性")
    namespace: dict[str, Any] = {"__builtins__": {}, "domain_agent_spec": domain_agent_spec}
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(module, "<historical-prompt-only>", "exec", flags=0x1000000), namespace)  # noqa: S102 - 只执行上述受限 AST
    return namespace["_system_prompt"]


async def export(
    ref: str, dataset: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    commit = (
        await asyncio.to_thread(
            subprocess.check_output,
            ["git", "rev-parse", "--verify", ref + "^{commit}"],
            cwd=root,
            text=True,
        )
    ).strip()
    source = await asyncio.to_thread(
        subprocess.check_output, ["git", "show", commit + ":" + SOURCE], cwd=root, text=True
    )
    build = historical_function(source)
    candidate = await runtime_snapshot(Settings())
    baseline = copy.deepcopy(candidate)
    accepts_intent = (
        "intent"
        in build.__code__.co_varnames[
            : build.__code__.co_argcount + build.__code__.co_kwonlyargcount
        ]
    )
    changed: set[str] = set()
    for name, prompt in baseline["prompts"].items():
        if name == "router":
            continue
        route, _, intent = name.partition(".")
        text = build(route, "zh-CN", **({"intent": intent} if accepts_intent else {}))
        if text != prompt["system"]:
            changed.add(name)
        prompt["system"] = text
    cases = [case for case in validate_dataset(dataset) if case["prompt_id"] in changed]
    if not cases:
        raise ValueError("历史 Prompt 文本与当前案例没有实际差异，不能伪装 A/B")
    baseline["prompt_version"] = "historical-text:" + commit[:12]
    baseline["provenance"] = {
        "commit": commit,
        "source": SOURCE,
        "function": "_system_prompt",
        "comparison_scope": "historical_prompt_text_under_current_tools_model_and_input_contract",
        "router_compared": False,
        "historical_runtime_replayed": False,
    }
    filtered = {
        **dataset,
        "version": str(dataset.get("version", "")) + "-historical-domain-only",
        "cases": cases,
    }
    return baseline, candidate, filtered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        results = asyncio.run(export(args.ref, json.loads(args.dataset.read_text())))
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, result in zip(("baseline", "candidate", "dataset"), results, strict=True):
            (args.output_dir / (name + ".json")).write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            )
        print(
            json.dumps(
                {
                    "passed": True,
                    "cases": len(results[2]["cases"]),
                    "historical_commit": results[0]["provenance"]["commit"],
                    "scope": results[0]["provenance"]["comparison_scope"],
                }
            )
        )
        return 0
    except (ValueError, TypeError, KeyError, OSError, subprocess.CalledProcessError):
        print(
            json.dumps({"passed": False, "error": "历史 Prompt 导出契约未通过"}, ensure_ascii=False)
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
