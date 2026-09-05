"""生成并校验 Agent 的不可变发布产物与配置契约清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class ReleaseManifestError(ValueError):
    """发布清单不符合不可变发布契约。"""


SERVICE_NAME = "fitness-agent-service"
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
CONFIG_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SHA256_HEX_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SENSITIVE_CONFIG_PATTERN = re.compile(r"(?i)(secret|password|token|api[_-]?key|private[_-]?key)")


def read_config_keys(path: Path) -> tuple[str, ...]:
    """从环境模板提取配置键，不读取也不保存等号右侧的 Secret 值。"""

    if not path.is_file():
        raise ReleaseManifestError(f"配置契约文件不存在：{path}")
    keys: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ReleaseManifestError(f"配置契约第 {line_number} 行缺少等号")
        key, _ = line.split("=", 1)
        key = key.strip()
        if not CONFIG_KEY_PATTERN.fullmatch(key):
            raise ReleaseManifestError(f"配置契约键名非法：{key}")
        keys.append(key)
    if len(keys) != len(set(keys)):
        raise ReleaseManifestError("配置契约中存在重复键名")
    return tuple(sorted(keys))


def config_contract_checksum(keys: tuple[str, ...]) -> str:
    """只对排序后的键名计算摘要，避免把配置值或 Secret 带入发布清单。"""

    payload = ("\n".join(keys) + "\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def canonical_digest(value: Any) -> str:
    """对版本化元数据做确定性摘要；调用方应只传非敏感内容。"""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def artifact_tree_digest(path: Path) -> tuple[str, int, int]:
    """计算模型目录的确定性摘要、文件数和字节数。

    摘要只包含相对文件名、文件内容摘要和大小，不包含机器绝对路径。
    符号链接只允许指向文件，并按目标文件内容计算；目录链接和悬空链接
    直接失败，避免把构建范围悄悄扩展到工作区之外。
    """

    if not path.exists():
        raise ReleaseManifestError(f"模型制品不存在：{path}")
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return f"sha256:{digest}", 1, path.stat().st_size
    if not path.is_dir():
        raise ReleaseManifestError(f"模型制品不是文件或目录：{path}")

    records: list[str] = []
    file_count = 0
    byte_count = 0
    for root, directories, files in os.walk(path, followlinks=False):
        directories[:] = sorted(
            directory for directory in directories if not (Path(root) / directory).is_symlink()
        )
        for filename in sorted(files):
            item = Path(root) / filename
            if item.is_symlink():
                target = item.resolve()
                if not target.exists() or target.is_dir():
                    raise ReleaseManifestError(f"模型制品包含无效文件链接：{item}")
                source = target
            else:
                source = item
            data = source.read_bytes()
            relative = item.relative_to(path).as_posix()
            digest = hashlib.sha256(data).hexdigest()
            records.append(f"{relative}\t{len(data)}\t{digest}\n")
            file_count += 1
            byte_count += len(data)
    if not records:
        raise ReleaseManifestError(f"模型制品目录为空：{path}")
    tree_digest = hashlib.sha256("".join(records).encode("utf-8")).hexdigest()
    return f"sha256:{tree_digest}", file_count, byte_count


def prompt_digests(prompts: Mapping[str, str]) -> dict[str, str]:
    """生成 Prompt 模板摘要，不把用户输入和动态检索正文放入清单。"""

    if not prompts or any(
        not str(name).strip() or not isinstance(value, str) for name, value in prompts.items()
    ):
        raise ReleaseManifestError("Prompt 摘要输入必须是非空名称到字符串模板的映射")
    return {
        str(name): canonical_digest(value)
        for name, value in sorted(prompts.items(), key=lambda item: str(item[0]))
    }


def build_manifest_v2(
    *,
    environment: str,
    source_commit: str,
    image: str,
    config_keys: tuple[str, ...],
    prompts: Mapping[str, str],
    safe_config: Mapping[str, Any],
    model_artifacts: Mapping[str, Mapping[str, Any]],
    tool_schemas: Mapping[str, Any],
    eval_release_id: str,
    eval_dataset_digest: str,
    eval_thresholds_digest: str,
    index_build_id: str | None = None,
    service_version: str | None = None,
) -> dict[str, Any]:
    """构建可追溯的 v2 清单，明确拒绝把 Secret 当作配置摘要输入。"""

    if any(SENSITIVE_CONFIG_PATTERN.search(str(key)) for key in safe_config):
        raise ReleaseManifestError("safe_config 包含疑似敏感配置键，不能写入发布清单")
    if not eval_release_id.strip():
        raise ReleaseManifestError("eval_release_id 不能为空")
    for value, name in (
        (eval_dataset_digest, "eval_dataset_digest"),
        (eval_thresholds_digest, "eval_thresholds_digest"),
    ):
        if not SHA256_HEX_PATTERN.fullmatch(value):
            raise ReleaseManifestError(f"{name} 必须是 sha256 摘要")
    normalized_artifacts: dict[str, dict[str, Any]] = {}
    for name, artifact in sorted(model_artifacts.items(), key=lambda item: str(item[0])):
        if not isinstance(artifact, Mapping) or not SHA256_HEX_PATTERN.fullmatch(
            str(artifact.get("digest", ""))
        ):
            raise ReleaseManifestError(f"模型制品 {name} 缺少合法 digest")
        normalized_artifacts[str(name)] = dict(artifact)
    manifest = {
        "schema_version": 2,
        "service": SERVICE_NAME,
        "environment": environment.strip().lower(),
        "service_version": (service_version or source_commit).strip(),
        "source_commit": source_commit.strip().lower(),
        "image": image.strip(),
        "config_contract_sha256": config_contract_checksum(config_keys),
        "required_config": list(config_keys),
        "components": {
            "prompt_digests": prompt_digests(prompts),
            "safe_config_digest": canonical_digest(dict(sorted(safe_config.items()))),
            "model_artifacts": normalized_artifacts,
            "tool_schema_digests": {
                str(name): canonical_digest(schema)
                for name, schema in sorted(tool_schemas.items(), key=lambda item: str(item[0]))
            },
            "evaluation": {
                "eval_release_id": eval_release_id.strip(),
                "dataset_digest": eval_dataset_digest,
                "thresholds_digest": eval_thresholds_digest,
            },
            "index_build_id": index_build_id.strip() if index_build_id else None,
        },
    }
    validate_manifest(manifest, expected_config_keys=config_keys)
    return manifest


def build_manifest(
    *,
    environment: str,
    source_commit: str,
    image: str,
    config_keys: tuple[str, ...],
    service_version: str | None = None,
) -> dict[str, Any]:
    """构建不包含 Secret、时间戳和机器路径的确定性发布清单。"""

    normalized_environment = environment.strip().lower()
    normalized_commit = source_commit.strip().lower()
    normalized_image = image.strip()
    version = (service_version or normalized_commit).strip()
    manifest = {
        "schema_version": 1,
        "service": SERVICE_NAME,
        "environment": normalized_environment,
        "service_version": version,
        "source_commit": normalized_commit,
        "image": normalized_image,
        "config_contract_sha256": config_contract_checksum(config_keys),
        "required_config": list(config_keys),
    }
    validate_manifest(manifest, expected_config_keys=config_keys)
    return manifest


def validate_manifest(
    manifest: Any,
    *,
    expected_config_keys: tuple[str, ...],
    expected_environment: str | None = None,
) -> None:
    """校验发布清单，生产和预发布拒绝可变标签及版本不一致的镜像。"""

    if not isinstance(manifest, dict):
        raise ReleaseManifestError("发布清单必须是 JSON 对象")
    schema_version = manifest.get("schema_version")
    if schema_version not in {1, 2}:
        raise ReleaseManifestError("不支持的发布清单 schema_version")
    if manifest.get("service") != SERVICE_NAME:
        raise ReleaseManifestError("发布清单 service 不属于 Agent 服务")

    environment = manifest.get("environment")
    service_version = manifest.get("service_version")
    source_commit = manifest.get("source_commit")
    image = manifest.get("image")
    contract_checksum = manifest.get("config_contract_sha256")
    required_config = manifest.get("required_config")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (environment, service_version, source_commit, image, contract_checksum)
    ):
        raise ReleaseManifestError("发布清单缺少必需字段")
    if not isinstance(required_config, list) or any(
        not isinstance(key, str) for key in required_config
    ):
        raise ReleaseManifestError("required_config 必须是字符串数组")
    assert isinstance(environment, str)
    assert isinstance(service_version, str)
    assert isinstance(source_commit, str)
    assert isinstance(image, str)
    assert isinstance(contract_checksum, str)
    if tuple(required_config) != expected_config_keys:
        raise ReleaseManifestError("required_config 与环境配置契约不一致")
    if contract_checksum != config_contract_checksum(expected_config_keys):
        raise ReleaseManifestError("配置契约摘要不匹配")
    if expected_environment is not None and environment != expected_environment:
        raise ReleaseManifestError(
            f"环境不符合预期：expected={expected_environment}, actual={environment}"
        )

    if environment in {"staging", "production"}:
        if not COMMIT_PATTERN.fullmatch(source_commit):
            raise ReleaseManifestError("预发布和生产的 source_commit 必须是 40 位 Git SHA")
        if service_version != source_commit:
            raise ReleaseManifestError("预发布和生产的 service_version 必须绑定 source_commit")
        if "@" not in image or not SHA256_PATTERN.fullmatch(image.rsplit("@", 1)[1]):
            raise ReleaseManifestError(
                "预发布和生产的 image 必须使用 sha256 digest，禁止只使用 tag"
            )
    elif not SHA256_PATTERN.fullmatch(contract_checksum):
        raise ReleaseManifestError("config_contract_sha256 格式非法")

    if schema_version == 2:
        _validate_v2_components(manifest.get("components"), environment=environment)


def _validate_v2_components(components: Any, *, environment: str = "local") -> None:
    """校验 v2 组件摘要完整性，不接受隐式缺省以免清单看似通过。"""

    if not isinstance(components, Mapping):
        raise ReleaseManifestError("v2 发布清单缺少 components")
    prompts = components.get("prompt_digests")
    if not isinstance(prompts, Mapping) or not prompts:
        raise ReleaseManifestError("v2 发布清单缺少 Prompt 摘要")
    if any(
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(digest, str)
        or not SHA256_HEX_PATTERN.fullmatch(digest)
        for name, digest in prompts.items()
    ):
        raise ReleaseManifestError("v2 Prompt 摘要格式非法")
    if not SHA256_HEX_PATTERN.fullmatch(str(components.get("safe_config_digest", ""))):
        raise ReleaseManifestError("v2 非敏感配置摘要格式非法")
    models = components.get("model_artifacts")
    if not isinstance(models, Mapping) or not models:
        raise ReleaseManifestError("v2 发布清单缺少模型制品摘要")
    for name, artifact in models.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(artifact, Mapping):
            raise ReleaseManifestError("v2 模型制品摘要格式非法")
        if not SHA256_HEX_PATTERN.fullmatch(str(artifact.get("digest", ""))):
            raise ReleaseManifestError(f"v2 模型制品 {name} digest 格式非法")
    schemas = components.get("tool_schema_digests")
    if not isinstance(schemas, Mapping) or any(
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(digest, str)
        or not SHA256_HEX_PATTERN.fullmatch(digest)
        for name, digest in schemas.items()
    ):
        raise ReleaseManifestError("v2 Tool Schema 摘要格式非法")
    evaluation = components.get("evaluation")
    if (
        not isinstance(evaluation, Mapping)
        or not str(evaluation.get("eval_release_id", "")).strip()
    ):
        raise ReleaseManifestError("v2 发布清单缺少 eval_release_id")
    for key in ("dataset_digest", "thresholds_digest"):
        if not SHA256_HEX_PATTERN.fullmatch(str(evaluation.get(key, ""))):
            raise ReleaseManifestError(f"v2 评测 {key} 格式非法")
    index_build_id = components.get("index_build_id")
    if index_build_id is not None and (
        not isinstance(index_build_id, str) or not index_build_id.strip()
    ):
        raise ReleaseManifestError("v2 index_build_id 不能为空字符串")
    if environment in {"staging", "production"} and not index_build_id:
        raise ReleaseManifestError("预发布和生产的 v2 Manifest 必须绑定 index_build_id")


def load_manifest(path: Path) -> dict[str, Any]:
    """读取 JSON 发布清单并提供稳定的错误信息。"""

    if not path.is_file():
        raise ReleaseManifestError(f"发布清单不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(f"无法读取发布清单：{path}") from exc
    if not isinstance(payload, dict):
        raise ReleaseManifestError("发布清单必须是 JSON 对象")
    return payload


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """写出确定性 JSON，便于审计、比对和作为部署流水线制品保存。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def load_components(path: Path) -> dict[str, Any]:
    """读取 v2 组件输入并做结构校验；不会在错误信息中回显具体值。"""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(f"无法读取 v2 组件文件：{path}") from exc
    if not isinstance(payload, dict):
        raise ReleaseManifestError("v2 组件文件必须是 JSON 对象")
    required = (
        "prompts",
        "safe_config",
        "model_artifacts",
        "tool_schemas",
        "eval_release_id",
        "eval_dataset_digest",
        "eval_thresholds_digest",
    )
    missing = [key for key in required if key not in payload]
    if missing:
        raise ReleaseManifestError("v2 组件文件缺少字段：" + ", ".join(missing))
    for key in ("prompts", "safe_config", "model_artifacts", "tool_schemas"):
        if not isinstance(payload[key], Mapping):
            raise ReleaseManifestError(f"v2 组件 {key} 必须是 JSON 对象")
    for key in ("eval_release_id", "eval_dataset_digest", "eval_thresholds_digest"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise ReleaseManifestError(f"v2 组件 {key} 必须是非空字符串")
    if (
        "index_build_id" in payload
        and payload["index_build_id"] is not None
        and not isinstance(payload["index_build_id"], str)
    ):
        raise ReleaseManifestError("v2 组件 index_build_id 必须是字符串或 null")
    return payload


def build_parser() -> argparse.ArgumentParser:
    """构造清单生成和校验命令。"""

    parser = argparse.ArgumentParser(description="生成或校验 Agent 不可变发布清单")
    parser.add_argument("--manifest", type=Path, required=True, help="发布清单 JSON 路径")
    parser.add_argument(
        "--config-contract",
        type=Path,
        required=True,
        help="环境配置模板路径，只读取键名计算契约摘要",
    )
    parser.add_argument("--expected-environment", default="", help="要求匹配的环境名称")
    parser.add_argument("--build", action="store_true", help="生成清单后立即校验")
    parser.add_argument(
        "--schema-version",
        type=int,
        choices=(1, 2),
        default=1,
        help="生成清单版本；v2 需要 --components",
    )
    parser.add_argument(
        "--components",
        type=Path,
        help="v2 组件 JSON；只读取受控 Prompt/非敏感配置/制品和评测摘要输入",
    )
    parser.add_argument("--environment", default="", help="生成清单的环境")
    parser.add_argument("--source-commit", default="", help="生成清单的 40 位 Git SHA")
    parser.add_argument(
        "--service-version", default="", help="服务版本；生产默认绑定 source-commit"
    )
    parser.add_argument("--image", default="", help="镜像引用，预发布/生产必须包含 @sha256:digest")
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行入口；失败时不打印配置值和镜像内部信息。"""

    try:
        args = build_parser().parse_args(argv)
        config_keys = read_config_keys(args.config_contract)
        if args.build:
            required = {
                "environment": args.environment,
                "source_commit": args.source_commit,
                "image": args.image,
            }
            missing = [name for name, value in required.items() if not str(value).strip()]
            if missing:
                raise ReleaseManifestError(f"生成清单缺少参数：{', '.join(missing)}")
            if args.schema_version == 2:
                if args.components is None:
                    raise ReleaseManifestError("生成 v2 清单必须提供 --components")
                components = load_components(args.components)
                manifest = build_manifest_v2(
                    environment=args.environment,
                    source_commit=args.source_commit,
                    image=args.image,
                    service_version=args.service_version or None,
                    config_keys=config_keys,
                    prompts=components["prompts"],
                    safe_config=components["safe_config"],
                    model_artifacts=components["model_artifacts"],
                    tool_schemas=components["tool_schemas"],
                    eval_release_id=components["eval_release_id"],
                    eval_dataset_digest=components["eval_dataset_digest"],
                    eval_thresholds_digest=components["eval_thresholds_digest"],
                    index_build_id=components.get("index_build_id"),
                )
            else:
                manifest = build_manifest(
                    environment=args.environment,
                    source_commit=args.source_commit,
                    image=args.image,
                    service_version=args.service_version or None,
                    config_keys=config_keys,
                )
            write_manifest(args.manifest, manifest)
        else:
            manifest = load_manifest(args.manifest)
            validate_manifest(
                manifest,
                expected_config_keys=config_keys,
                expected_environment=args.expected_environment.strip() or None,
            )
        print(f"Agent 发布清单校验通过：{args.manifest}")
        return 0
    except ReleaseManifestError as exc:
        print(f"Agent 发布清单校验失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
