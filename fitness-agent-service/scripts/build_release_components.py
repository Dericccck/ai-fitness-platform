"""生成 v2 Release Manifest 所需的真实组件摘要输入。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.release_manifest_check import ReleaseManifestError, artifact_tree_digest
except ModuleNotFoundError:  # 允许直接执行 `python scripts/build_release_components.py`
    from release_manifest_check import ReleaseManifestError, artifact_tree_digest


def _pair(value: str, *, option: str) -> tuple[str, str]:
    if "=" not in value:
        raise ReleaseManifestError(f"{option} 必须使用 NAME=VALUE 格式")
    name, payload = value.split("=", 1)
    if not name.strip() or not payload.strip():
        raise ReleaseManifestError(f"{option} 的名称和值不能为空")
    return name.strip(), payload.strip()


def _unique_pairs(values: list[str], *, option: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name, payload = _pair(value, option=option)
        if name in result:
            raise ReleaseManifestError(f"{option} 存在重复名称：{name}")
        result[name] = payload
    return result


def _file_digest(path: Path) -> str:
    if not path.is_file():
        raise ReleaseManifestError(f"评测发布文件不存在：{path}")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load_eval_release(path: Path) -> dict[str, str]:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(f"无法读取评测发布文件：{path}") from exc
    if not isinstance(raw, dict):
        raise ReleaseManifestError("评测发布文件必须是 JSON 对象")
    required = (
        "eval_release_id",
        "dataset_path",
        "dataset_digest",
        "thresholds_path",
        "thresholds_digest",
    )
    if any(not isinstance(raw.get(key), str) or not raw[key].strip() for key in required):
        raise ReleaseManifestError("评测发布文件缺少必需字段")
    release_dir = path.parent.resolve()
    dataset = (release_dir / raw["dataset_path"]).resolve()
    thresholds = (release_dir / raw["thresholds_path"]).resolve()
    if _file_digest(dataset) != raw["dataset_digest"]:
        raise ReleaseManifestError("评测集摘要与评测发布文件不一致")
    if _file_digest(thresholds) != raw["thresholds_digest"]:
        raise ReleaseManifestError("阈值摘要与评测发布文件不一致")
    return {
        "eval_release_id": raw["eval_release_id"],
        "eval_dataset_digest": raw["dataset_digest"],
        "eval_thresholds_digest": raw["thresholds_digest"],
    }


def _load_json_file(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(f"无法读取 {label}：{path}") from exc


def _safe_config(values: dict[str, str]) -> dict[str, Any]:
    sensitive = ("secret", "password", "token", "api_key", "private_key")
    result: dict[str, Any] = {}
    for key, value in values.items():
        if any(marker in key.lower() for marker in sensitive):
            raise ReleaseManifestError(f"safe config 包含敏感键：{key}")
        try:
            result[key] = json.loads(value)
        except json.JSONDecodeError:
            result[key] = value
    return result


def build_components(args: argparse.Namespace) -> dict[str, Any]:
    """读取受控组件来源并生成不含机器路径的 JSON 输入。"""

    release = _load_eval_release(args.eval_release)
    prompts = _unique_pairs(args.prompt, option="--prompt")
    if not prompts:
        raise ReleaseManifestError("至少需要一个 --prompt")
    model_paths = _unique_pairs(args.model_artifact, option="--model-artifact")
    if not model_paths:
        raise ReleaseManifestError("至少需要一个 --model-artifact；不能伪造模型摘要")
    schemas = _unique_pairs(args.tool_schema, option="--tool-schema")
    tools = {
        name: _load_json_file(Path(path), label="Tool Schema") for name, path in schemas.items()
    }
    artifacts: dict[str, dict[str, Any]] = {}
    for name, path_text in model_paths.items():
        digest, file_count, byte_count = artifact_tree_digest(Path(path_text))
        artifacts[name] = {
            "digest": digest,
            "kind": "filesystem",
            "file_count": file_count,
            "byte_count": byte_count,
        }
    return {
        "schema_version": 1,
        "prompts": {name: Path(path).read_text(encoding="utf-8") for name, path in prompts.items()},
        "safe_config": _safe_config(_unique_pairs(args.safe_config, option="--safe-config")),
        "model_artifacts": artifacts,
        "tool_schemas": tools,
        **release,
        "index_build_id": args.index_build_id.strip() if args.index_build_id else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="组件 JSON 输出路径")
    parser.add_argument("--eval-release", type=Path, required=True, help="正式评测发布描述")
    parser.add_argument("--prompt", action="append", default=[], help="Prompt：NAME=PATH")
    parser.add_argument("--model-artifact", action="append", default=[], help="模型制品：NAME=PATH")
    parser.add_argument("--tool-schema", action="append", default=[], help="Tool Schema：NAME=PATH")
    parser.add_argument("--safe-config", action="append", default=[], help="非敏感配置：NAME=VALUE")
    parser.add_argument("--index-build-id", default=None, help="已完成且可查询的索引构建 ID")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        payload = build_components(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"v2 组件摘要已生成：{args.output}")
        return 0
    except (OSError, ReleaseManifestError) as exc:
        print(f"v2 组件摘要生成失败：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
