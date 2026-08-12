"""上传知识文档使用的私有暂存存储。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast


class DocumentStorageError(RuntimeError):
    """暂存存储无法安全读取或写入文档。"""


class DocumentStorage(Protocol):
    """本地开发和对象存储适配器共享的存储契约。"""

    def store(self, job_id: str, file_name: str, content: bytes, *, content_type: str) -> str:
        """持久化不可变字节，并返回不透明存储键。"""

    def read(self, key: str) -> bytes:
        """根据不透明存储键读取对象。"""


class LocalDocumentStorage:
    """首个部署版本使用的本地存储边界实现。

    The job stores an opaque key rather than a user-provided path. Production can replace
    this class with an S3/OSS adapter without changing review, retry, or indexing logic.
    """

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def store(self, job_id: str, file_name: str, content: bytes, *, content_type: str = "") -> str:
        """写入一个不可变暂存对象，并返回不透明相对键。"""

        suffix = PurePosixPath(file_name).suffix.lower()
        if not suffix or len(suffix) > 10 or not suffix[1:].isalnum():
            raise DocumentStorageError("uploaded file must have a safe extension")
        key = f"{job_id}{suffix}"
        target = self._safe_path(key)
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(target)
        except OSError as exc:
            raise DocumentStorageError("could not persist uploaded document") from exc
        return key

    def read(self, key: str) -> bytes:
        """将对象解析到配置的暂存根目录下后再读取。"""

        try:
            return self._safe_path(key).read_bytes()
        except OSError as exc:
            raise DocumentStorageError("could not read staged document") from exc

    def _safe_path(self, key: str) -> Path:
        """在访问文件系统前拒绝路径穿越和绝对路径。"""

        candidate = (self._root / key).resolve()
        if candidate.parent != self._root or Path(key).is_absolute():
            raise DocumentStorageError("invalid storage key")
        return candidate


class S3DocumentStorage:
    """兼容 S3 的对象存储适配器，包括 MinIO 和 OSS 网关。

    boto3 is synchronous, so network calls are moved to worker threads. The rest of the
    Agent service only sees the small storage contract and does not depend on a vendor SDK.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        bucket: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        if not endpoint_url or not bucket or not access_key or not secret_key:
            raise ValueError("S3 storage requires endpoint, bucket, access key, and secret key")
        import boto3  # type: ignore[import-untyped]

        self._bucket = bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def store(self, job_id: str, file_name: str, content: bytes, *, content_type: str = "") -> str:
        suffix = PurePosixPath(file_name).suffix.lower()
        if not suffix or len(suffix) > 10 or not suffix[1:].isalnum():
            raise DocumentStorageError("uploaded file must have a safe extension")
        key = f"knowledge/{job_id}{suffix}"
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType=content_type or "application/octet-stream",
            )
        except Exception as exc:
            raise DocumentStorageError("could not persist uploaded document") from exc
        return key

    def read(self, key: str) -> bytes:
        if not _is_safe_s3_key(key):
            raise DocumentStorageError("invalid storage key")
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return cast(bytes, response["Body"].read())
        except Exception as exc:
            raise DocumentStorageError("could not read staged document") from exc


def _is_safe_s3_key(key: str) -> bool:
    """只允许使用本服务生成的键，禁止任意对象路径。"""

    parts = PurePosixPath(key).parts
    return bool(parts) and parts[0] == "knowledge" and ".." not in parts and len(parts) == 2
