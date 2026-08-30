"""生成并校验 Agent 的不可变发布产物与配置契约清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


class ReleaseManifestError(ValueError):
    """发布清单不符合不可变发布契约。"""


SERVICE_NAME = "fitness-agent-service"
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
CONFIG_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
    if manifest.get("schema_version") != 1:
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
    parser.add_argument("--environment", default="", help="生成清单的环境")
    parser.add_argument("--source-commit", default="", help="生成清单的 40 位 Git SHA")
    parser.add_argument(
        "--service-version", default="", help="服务版本；生产默认绑定 source-commit"
    )
    parser.add_argument("--image", default="", help="镜像引用，预发布/生产必须包含 @sha256:digest")
    return parser


def main() -> int:
    """命令行入口；失败时不打印配置值和镜像内部信息。"""

    try:
        args = build_parser().parse_args()
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
