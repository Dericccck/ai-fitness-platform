from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import shutil
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from PIL import Image

from app.media.metadata import MediaValidationError, inspect_stream
from app.media.storage import MediaStorageError, OssMediaStorage, StoredVersion


class FakeBucket:
    def __init__(self) -> None:
        self.head_calls = []
        self.copy_calls = []

    def head_object(self, key, params=None):
        self.head_calls.append((key, params))
        version = (params or {}).get("versionId", "source-version")
        return SimpleNamespace(
            headers={"x-oss-version-id": version}, content_length=12, content_type="video/mp4"
        )

    def copy_object(self, bucket, source, target, params=None):
        self.copy_calls.append((bucket, source, target, params))
        return SimpleNamespace(headers={"x-oss-version-id": "final-version"})

    def sign_url(self, method, key, expires, params=None):
        assert method == "GET" and params == {"versionId": "final-version"}
        return "https://private-bucket.oss-cn-beijing.aliyuncs.com/object?signature=hidden"


def storage(bucket=None):
    return OssMediaStorage(
        endpoint="https://oss-cn-beijing.aliyuncs.com",
        bucket_name="private-bucket",
        region="cn-beijing",
        access_key_id="test-id",
        access_key_secret="test-secret",
        security_token="temporary-token",
        bucket=bucket or FakeBucket(),
    )


def test_post_policy_binds_exact_staging_key_size_and_temporary_token():
    policy = storage().post_policy(
        "staging/user/asset",
        max_bytes=1000,
        content_type="video/mp4",
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )
    fields = policy.form_data
    decoded = json.loads(base64.b64decode(fields["policy"]))
    assert {"key": "staging/user/asset"} in decoded["conditions"]
    assert ["content-length-range", 1, 1000] in decoded["conditions"]
    assert {"x-oss-security-token": "temporary-token"} in decoded["conditions"]
    key = b"aliyun_v4test-secret"
    for part in ("20260601", "cn-beijing", "oss", "aliyun_v4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    assert (
        fields["x-oss-signature"]
        == hmac.new(key, fields["policy"].encode(), hashlib.sha256).hexdigest()
    )
    assert "temporary-token" not in repr(policy) and "signature" not in repr(policy)


@pytest.mark.parametrize(
    "key", ["final/user/asset", "staging/../asset", "staging/u/a/extra", "https://x"]
)
def test_policy_never_signs_arbitrary_or_final_objects(key):
    with pytest.raises(MediaStorageError):
        storage().post_policy(key, max_bytes=10, content_type="image/jpeg")


def test_copy_and_read_are_bound_to_immutable_object_versions():
    bucket = FakeBucket()
    service = storage(bucket)
    source = service.head("staging/user/asset")
    final = service.copy_to_final(source, "final/user/newasset")
    assert bucket.copy_calls == [
        (
            "private-bucket",
            "staging/user/asset",
            "final/user/newasset",
            {"versionId": "source-version"},
        )
    ]
    assert bucket.head_calls[-1] == ("final/user/newasset", {"versionId": "final-version"})
    assert final.version_id == "final-version"
    assert service.signed_read_url(final, ttl_seconds=120).startswith("https://private-bucket.")


def test_unversioned_bucket_or_untrusted_signed_host_fails_closed():
    bucket = FakeBucket()
    bucket.head_object = lambda *args, **kwargs: SimpleNamespace(
        headers={}, content_length=12, content_type="video/mp4"
    )
    with pytest.raises(MediaStorageError, match="版本控制"):
        storage(bucket).head("staging/user/asset")
    item = StoredVersion("staging/user/asset", "v1", 12, "video/mp4")
    with pytest.raises(MediaStorageError):
        storage().signed_read_url(item, ttl_seconds=120)


def test_delete_all_versions_paginates_and_never_deletes_a_neighboring_key():
    bucket = FakeBucket()
    deleted = []

    def list_versions(*, prefix, max_keys, key_marker, versionid_marker):
        if not key_marker:
            return SimpleNamespace(
                versions=[
                    SimpleNamespace(key=prefix, versionid="v1"),
                    SimpleNamespace(key=prefix + "-neighbor", versionid="other"),
                ],
                delete_marker=[],
                is_truncated=True,
                next_key_marker=prefix,
                next_versionid_marker="v1",
            )
        return SimpleNamespace(
            versions=[],
            delete_marker=[SimpleNamespace(key=prefix, versionid="marker-v2")],
            is_truncated=False,
        )

    bucket.list_object_versions = list_versions
    bucket.delete_object = lambda key, params: deleted.append((key, params["versionId"]))
    assert storage(bucket).delete_all_versions("final/user/asset") == 2
    assert deleted == [("final/user/asset", "v1"), ("final/user/asset", "marker-v2")]


def test_many_versions_are_deleted_in_bounded_batches_before_retry():
    bucket = FakeBucket()
    deleted = []
    page = 0

    def list_versions(*, prefix, max_keys, key_marker, versionid_marker):
        nonlocal page
        page += 1
        return SimpleNamespace(
            versions=[SimpleNamespace(key=prefix, versionid=f"v{page}")],
            delete_marker=[],
            is_truncated=True,
            next_key_marker=prefix,
            next_versionid_marker=f"v{page}",
        )

    bucket.list_object_versions = list_versions
    bucket.delete_object = lambda key, params: deleted.append(params["versionId"])
    with pytest.raises(MediaStorageError, match="尚未清理完毕"):
        storage(bucket).delete_all_versions("final/user/asset")
    assert len(deleted) == 100 and page == 100


def test_inspect_image_detects_spoofing_truncation_and_pixel_limit():
    result = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(result, format="PNG")
    data = result.getvalue()
    metadata = inspect_stream(
        io.BytesIO(data), declared_type="image/png", expected_size=len(data), max_bytes=10000
    )
    assert (metadata.width, metadata.height, metadata.sha256) == (
        8,
        6,
        hashlib.sha256(data).hexdigest(),
    )
    with pytest.raises(MediaValidationError):
        inspect_stream(
            io.BytesIO(data), declared_type="image/jpeg", expected_size=len(data), max_bytes=10000
        )
    with pytest.raises(MediaValidationError):
        inspect_stream(
            io.BytesIO(data),
            declared_type="image/png",
            expected_size=len(data) + 1,
            max_bytes=10000,
        )
    with pytest.raises(MediaValidationError):
        inspect_stream(
            io.BytesIO(data),
            declared_type="image/png",
            expected_size=len(data),
            max_bytes=10000,
            max_pixels=47,
        )


def test_inspect_video_checks_actual_duration_without_extracting_frames(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe is required for this contract test")
    path = tmp_path / "video.mp4"
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=5:d=3",
            "-c:v",
            "libx264",
            "-y",
            str(path),
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    if result.returncode:
        pytest.skip("local ffmpeg lacks a test H.264 encoder")
    data = path.read_bytes()
    metadata = inspect_stream(
        io.BytesIO(data),
        declared_type="video/mp4",
        expected_size=len(data),
        max_bytes=100000,
        ffprobe_binary=ffprobe,
    )
    assert metadata.duration_seconds == 3 and (metadata.width, metadata.height) == (64, 64)
    default_probe = inspect_stream(
        io.BytesIO(data), declared_type="video/mp4", expected_size=len(data), max_bytes=100000
    )
    assert default_probe.duration_seconds == 3
    with pytest.raises(MediaValidationError):
        inspect_stream(
            io.BytesIO(data),
            declared_type="video/mp4",
            expected_size=len(data),
            max_bytes=100000,
            max_duration_seconds=2,
            ffprobe_binary=ffprobe,
        )


def test_metadata_subprocess_does_not_inherit_service_secrets(monkeypatch):
    import json
    from types import SimpleNamespace

    observed = {}
    monkeypatch.setenv("FITNESS_PRIVATE_TEST_SECRET", "must-not-leave-parent")

    def run(*args, **kwargs):
        observed.update(kwargs["env"])
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ok": True, "width": 1, "height": 1, "duration_seconds": None}),
        )

    monkeypatch.setattr("app.media.metadata.subprocess.run", run)
    data = b"test"
    result = inspect_stream(
        io.BytesIO(data), declared_type="image/png", expected_size=4, max_bytes=4
    )
    assert result.width == 1 and "FITNESS_PRIVATE_TEST_SECRET" not in observed
