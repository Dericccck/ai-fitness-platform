"""对已脱敏的离线案例数据运行 TruLens 语义评估。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from app.core.config import get_settings

from .trulens_eval import (
    TruLensEvaluationError,
    TruLensEvaluator,
    bind_eval_release,
    evaluation_run_summary,
    file_fingerprint,
    load_cases,
    load_eval_release,
    load_thresholds,
    load_trace_cases,
    validate_thresholds,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--cases", type=Path, help="离线评测案例 JSON")
    source_group.add_argument("--traces", type=Path, help="已脱敏的 OTLP/TruLens Trace JSON")
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--judge", action="store_true", help="调用已配置的 DeepSeek Judge")
    parser.add_argument("--no-persist", action="store_true", help="不写入 TruLens 数据库")
    parser.add_argument("--run-id", default=None, help="评测运行标识；未提供时自动生成")
    parser.add_argument(
        "--dataset-version",
        default=None,
        help="评测集版本；未提供时使用输入文件 SHA-256 摘要",
    )
    parser.add_argument(
        "--eval-release-id",
        default=None,
        help="正式评测发布号；未提供时读取 Settings 或 --release",
    )
    parser.add_argument(
        "--release",
        type=Path,
        help="正式评测发布描述 JSON；校验评测集和阈值摘要并绑定发布号",
    )
    parser.add_argument("--report", type=Path, help="将完整评测报告写入指定 JSON 文件")
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
        input_path = args.cases or args.traces
        assert input_path is not None
        eval_source = "cases" if args.cases else "traces"
        cases = load_cases(input_path) if args.cases else load_trace_cases(input_path)
        thresholds = load_thresholds(args.thresholds)
        release = (
            load_eval_release(args.release, input_path=input_path, thresholds_path=args.thresholds)
            if args.release
            else None
        )
        eval_release_id = (
            args.eval_release_id
            or settings.eval_release_id
            or (str(release["eval_release_id"]) if release else "")
        )
        if not eval_release_id.strip():
            raise TruLensEvaluationError(
                "必须提供 --eval-release-id、AGENT_EVAL_RELEASE_ID 或 --release"
            )
        if release and eval_release_id != release["eval_release_id"]:
            raise TruLensEvaluationError("eval_release_id 与评测发布描述不一致")
        if release:
            judge_config = release.get("judge", {})
            if not isinstance(judge_config, dict):
                raise TruLensEvaluationError("评测发布描述 judge 必须是 JSON 对象")
            if bool(judge_config.get("enabled", False)) != args.judge:
                raise TruLensEvaluationError("--judge 与评测发布描述的 Judge 开关不一致")
        cases = bind_eval_release(cases, eval_release_id)
        evaluator = TruLensEvaluator(settings, persist=not args.no_persist, judge=args.judge)
        results = [evaluator.evaluate_case(case) for case in cases]
        failures = validate_thresholds(results, thresholds)
    except TruLensEvaluationError as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    run_id = args.run_id or f"trulens-{uuid4().hex}"
    dataset_version = args.dataset_version or (
        str(release["dataset_digest"]) if release else file_fingerprint(input_path)
    )
    if release and dataset_version != release["dataset_digest"]:
        print(
            json.dumps(
                {"passed": False, "error": "dataset_version 与评测发布描述不一致"},
                ensure_ascii=False,
            )
        )
        return 2
    output = {
        "passed": not failures,
        "run_id": run_id,
        "source": eval_source,
        "dataset_version": dataset_version,
        "eval_release_id": eval_release_id,
        "eval_release": release,
        "thresholds": thresholds,
        "cases": results,
        "failures": failures,
        "summary": evaluation_run_summary(
            results,
            thresholds,
            source=eval_source,
            dataset_version=dataset_version,
            run_id=run_id,
            eval_release_id=eval_release_id,
        ),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if args.report:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            print(
                json.dumps(
                    {"passed": False, "error": f"无法写入评测报告：{exc}"}, ensure_ascii=False
                )
            )
            return 2
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
