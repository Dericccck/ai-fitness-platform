"""Private staging storage for uploaded knowledge documents."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


class DocumentStorageError(RuntimeError):
    """The staging storage cannot safely read or write a document."""


class LocalDocumentStorage:
    """Local implementation of the storage boundary used by the first deployment.

    The job stores an opaque key rather than a user-provided path. Production can replace
    this class with an S3/OSS adapter without changing review, retry, or indexing logic.
    """

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def store(self, job_id: str, file_name: str, content: bytes) -> str:
        """Write one immutable staged object and return its opaque relative key."""

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
        """Read an object only after resolving it below the configured staging root."""

        try:
            return self._safe_path(key).read_bytes()
        except OSError as exc:
            raise DocumentStorageError("could not read staged document") from exc

    def _safe_path(self, key: str) -> Path:
        """Reject traversal and absolute paths before touching the filesystem."""

        candidate = (self._root / key).resolve()
        if candidate.parent != self._root or Path(key).is_absolute():
            raise DocumentStorageError("invalid storage key")
        return candidate
