"""阿里云 OSS 私有媒体存储；上传签名仅允许写入单个暂存对象。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import oss2  # type: ignore[import-untyped]


class MediaStorageError(RuntimeError):
    """不暴露OSS签名、地址或供应商请求正文。"""


class MediaObjectNotFound(MediaStorageError):
    """尚未上传暂存对象；固定版本丢失不属于可重传情况。"""


@dataclass(frozen=True)
class PostPolicy:
    host: str
    form_data: dict[str, str] = field(repr=False)
    expires_at: datetime
    file_field_name: str = "file"


@dataclass(frozen=True)
class StoredVersion:
    key: str = field(repr=False)
    version_id: str = field(repr=False)
    size: int
    content_type: str


_KEY = re.compile(r"(?:staging|final)/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+\Z")
_VERSION = re.compile(r"[A-Za-z0-9._~-]{1,256}\Z")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]\Z")
_REGION = re.compile(r"[a-z0-9-]{2,32}\Z")


def _validate_key(key: str, prefix: str | None = None) -> None:
    if not _KEY.fullmatch(key) or (prefix is not None and not key.startswith(prefix + "/")):
        raise MediaStorageError("无效媒体存储标识")


def _validate_version(version_id: str) -> None:
    if version_id in {"", "null"} or not _VERSION.fullmatch(version_id):
        raise MediaStorageError("存储桶必须启用版本控制")


class OssMediaStorage:
    """OSS V4 POST直传、版本固定和受限读取；不提供任意URL抓取。"""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket_name: str,
        region: str,
        access_key_id: str,
        access_key_secret: str,
        security_token: str = "",
        bucket: Any | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        try:
            valid = (
                parsed.scheme == "https"
                and parsed.hostname == f"oss-{region}.aliyuncs.com"
                and parsed.port in {None, 443}
                and not parsed.username
                and not parsed.password
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
                and _BUCKET.fullmatch(bucket_name)
                and _REGION.fullmatch(region)
                and access_key_id
                and access_key_secret
            )
        except ValueError:
            valid = False
        if not valid:
            raise MediaStorageError("OSS 媒体存储配置无效")
        self.bucket_name = bucket_name
        self.region = region
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._security_token = security_token
        self.host = f"https://{bucket_name}.oss-{region}.aliyuncs.com"
        auth = (
            oss2.StsAuth(access_key_id, access_key_secret, security_token, auth_version="v4")
            if security_token
            else oss2.AuthV4(access_key_id, access_key_secret)
        )
        self._bucket = bucket or oss2.Bucket(
            auth, endpoint, bucket_name, region=region, connect_timeout=10
        )

    def post_policy(
        self,
        key: str,
        *,
        max_bytes: int,
        content_type: str,
        ttl_seconds: int = 900,
        now: datetime | None = None,
    ) -> PostPolicy:
        _validate_key(key, "staging")
        if not 1 <= max_bytes <= 100 * 1024 * 1024 or not 1 <= ttl_seconds <= 900:
            raise MediaStorageError("媒体上传限额无效")
        if content_type not in {
            "image/jpeg",
            "image/png",
            "image/webp",
            "video/mp4",
            "video/quicktime",
        }:
            raise MediaStorageError("媒体类型不受支持")
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise MediaStorageError("签名时间必须带时区")
        now = now.astimezone(UTC)
        day = now.strftime("%Y%m%d")
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        expires = now + timedelta(seconds=ttl_seconds)
        credential = f"{self._access_key_id}/{day}/{self.region}/oss/aliyun_v4_request"
        fields = {
            "key": key,
            "Content-Type": content_type,
            "success_action_status": "201",
            "x-oss-signature-version": "OSS4-HMAC-SHA256",
            "x-oss-credential": credential,
            "x-oss-date": timestamp,
        }
        if self._security_token:
            fields["x-oss-security-token"] = self._security_token
        policy = {
            "expiration": expires.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "conditions": [
                {"bucket": self.bucket_name},
                *[{k: v} for k, v in fields.items()],
                ["content-length-range", 1, max_bytes],
            ],
        }
        encoded = base64.b64encode(json.dumps(policy, separators=(",", ":")).encode()).decode()
        key_material = ("aliyun_v4" + self._access_key_secret).encode()
        for part in (day, self.region, "oss", "aliyun_v4_request"):
            key_material = hmac.new(key_material, part.encode(), hashlib.sha256).digest()
        fields["policy"] = encoded
        fields["x-oss-signature"] = hmac.new(
            key_material, encoded.encode(), hashlib.sha256
        ).hexdigest()
        return PostPolicy(self.host, fields, expires)

    def head(self, key: str, *, version_id: str | None = None) -> StoredVersion:
        _validate_key(key)
        if version_id is not None:
            _validate_version(version_id)
        try:
            result = self._bucket.head_object(
                key, params={"versionId": version_id} if version_id else None
            )
            actual_version = result.headers.get("x-oss-version-id", "")
            _validate_version(actual_version)
            if version_id is not None and actual_version != version_id:
                raise MediaStorageError("媒体对象版本不一致")
            if result.content_length is None or result.content_length <= 0:
                raise MediaStorageError("媒体对象为空")
            return StoredVersion(
                key, actual_version, result.content_length, result.content_type or ""
            )
        except MediaStorageError:
            raise
        except oss2.exceptions.NotFound as exc:
            if version_id is None:
                raise MediaObjectNotFound("暂存素材尚未上传") from exc
            raise MediaStorageError("固定媒体版本不可用") from exc
        except Exception as exc:
            raise MediaStorageError("无法读取媒体对象") from exc

    def open_version(self, version: StoredVersion) -> Any:
        _validate_key(version.key)
        _validate_version(version.version_id)
        try:
            return self._bucket.get_object(version.key, params={"versionId": version.version_id})
        except Exception as exc:
            raise MediaStorageError("无法读取媒体对象") from exc

    def open_version_range(self, version: StoredVersion, start: int, end: int) -> Any:
        _validate_key(version.key, "final")
        _validate_version(version.version_id)
        if not 0 <= start <= end < version.size:
            raise MediaStorageError("媒体读取范围无效")
        try:
            return self._bucket.get_object(
                version.key, byte_range=(start, end), params={"versionId": version.version_id}
            )
        except Exception as exc:
            raise MediaStorageError("无法读取媒体对象") from exc

    def put_gateway_upload(self, key: str, stream: Any, *, size: int, content_type: str) -> None:
        """Write one bounded server-mediated upload; caller must hold a durable one-shot claim."""
        _validate_key(key, "staging")
        if not 0 < size <= 100 * 1024 * 1024:
            raise MediaStorageError("媒体大小无效")
        try:
            result = self._bucket.put_object(key, stream, headers={"Content-Type": content_type})
            _validate_version(result.headers.get("x-oss-version-id", ""))
        except MediaStorageError:
            raise
        except Exception as exc:
            raise MediaStorageError("素材上传结果未确认") from exc

    def copy_to_final(self, source: StoredVersion, final_key: str) -> StoredVersion:
        _validate_key(source.key, "staging")
        _validate_key(final_key, "final")
        _validate_version(source.version_id)
        try:
            result = self._bucket.copy_object(
                self.bucket_name, source.key, final_key, params={"versionId": source.version_id}
            )
            version_id = result.headers.get("x-oss-version-id", "")
            _validate_version(version_id)
            final = self.head(final_key, version_id=version_id)
            if final.size != source.size:
                raise MediaStorageError("固化媒体大小不一致")
            return final
        except MediaStorageError:
            raise
        except Exception as exc:
            raise MediaStorageError("无法固化媒体对象") from exc

    def signed_read_url(self, version: StoredVersion, *, ttl_seconds: int) -> str:
        _validate_key(version.key, "final")
        _validate_version(version.version_id)
        if not 1 <= ttl_seconds <= 600:
            raise MediaStorageError("媒体访问时限无效")
        try:
            result = self._bucket.sign_url(
                "GET", version.key, ttl_seconds, params={"versionId": version.version_id}
            )
            parsed = urlsplit(result)
            if parsed.scheme != "https" or parsed.hostname != urlsplit(self.host).hostname:
                raise MediaStorageError("媒体访问地址无效")
            return str(result)
        except MediaStorageError:
            raise
        except Exception as exc:
            raise MediaStorageError("无法签发媒体访问地址") from exc

    def delete_version(self, key: str, version_id: str) -> None:
        """删除已知版本，不把添加删除标记误认为物理删除。"""
        _validate_key(key)
        _validate_version(version_id)
        try:
            self._bucket.delete_object(key, params={"versionId": version_id})
        except Exception as exc:
            raise MediaStorageError("无法删除媒体对象版本") from exc

    def delete_all_versions(self, key: str) -> int:
        """只清除精确 key 的全部版本及删除标记；任何分页异常都保留任务重试。"""
        _validate_key(key)
        versions: list[str] = []
        key_marker, version_marker = "", ""
        more_pages = False
        try:
            for _ in range(100):
                page = self._bucket.list_object_versions(
                    prefix=key,
                    max_keys=100,
                    key_marker=key_marker,
                    versionid_marker=version_marker,
                )
                for item in [*page.versions, *page.delete_marker]:
                    if item.key == key:
                        _validate_version(item.versionid)
                        versions.append(item.versionid)
                if not page.is_truncated:
                    break
                next_markers = (page.next_key_marker, page.next_versionid_marker)
                if not next_markers[0] or next_markers == (key_marker, version_marker):
                    raise MediaStorageError("媒体版本分页无效")
                key_marker, version_marker = next_markers
            else:
                # Commit bounded progress; the durable worker retries from the start.
                more_pages = True
            for version in versions:
                self.delete_version(key, version)
            if more_pages:
                if not versions:
                    raise MediaStorageError("媒体版本分页未能取得可清理的版本")
                raise MediaStorageError("媒体版本尚未清理完毕，请重试")
            return len(versions)
        except MediaStorageError:
            raise
        except Exception as exc:
            raise MediaStorageError("无法清理媒体对象版本") from exc
