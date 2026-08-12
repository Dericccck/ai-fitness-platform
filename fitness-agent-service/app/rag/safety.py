"""Structural safety checks before a document enters review or object storage."""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath


class DocumentSafetyError(ValueError):
    """The upload is malformed, unsafe, or violates archive expansion limits."""


@dataclass(frozen=True)
class SafetyScanResult:
    """Auditable result of deterministic checks performed before staging."""

    sha256: str
    status: str
    scanner_name: str


class StructuralDocumentScanner:
    """Reject obvious malformed files and ZIP bombs before parser or storage work.

    This is not an antivirus engine. Production deployments should decorate this scanner
    with ClamAV or a cloud malware service and persist that external verdict separately.
    """

    def __init__(self, *, max_uncompressed_bytes: int = 100 * 1024 * 1024) -> None:
        self.max_uncompressed_bytes = max_uncompressed_bytes

    def scan(self, file_name: str, content: bytes) -> SafetyScanResult:
        suffix = PurePosixPath(file_name).suffix.lower()
        if suffix in {".md", ".markdown", ".txt"}:
            self._scan_text(content)
        elif suffix == ".pdf":
            if not content.startswith(b"%PDF-"):
                raise DocumentSafetyError("PDF signature is invalid")
        elif suffix in {".docx", ".xlsx"}:
            self._scan_office_archive(content)
        else:
            raise DocumentSafetyError("file extension is not allowed")
        return SafetyScanResult(
            sha256=hashlib.sha256(content).hexdigest(),
            status="STRUCTURAL_VALIDATED",
            scanner_name="structural-v1",
        )

    @staticmethod
    def _scan_text(content: bytes) -> None:
        if b"\x00" in content:
            raise DocumentSafetyError("text document contains binary NUL bytes")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentSafetyError("text document must be UTF-8") from exc

    def _scan_office_archive(self, content: bytes) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                members = archive.infolist()
                if len(members) > 2000:
                    raise DocumentSafetyError("office archive contains too many members")
                expanded_size = 0
                for member in members:
                    parts = PurePosixPath(member.filename).parts
                    if PurePosixPath(member.filename).is_absolute() or ".." in parts:
                        raise DocumentSafetyError("office archive contains unsafe paths")
                    if member.flag_bits & 0x1:
                        raise DocumentSafetyError("encrypted office archives are not supported")
                    file_mode = (member.external_attr >> 16) & 0o170000
                    if file_mode == 0o120000:
                        raise DocumentSafetyError("office archive contains a symbolic link")
                    expanded_size += member.file_size
                    if expanded_size > self.max_uncompressed_bytes:
                        raise DocumentSafetyError("office archive exceeds expansion limit")
                    if PurePosixPath(member.filename).name == "vbaProject.bin":
                        raise DocumentSafetyError("macro-enabled office files are not supported")
                if archive.testzip() is not None:
                    raise DocumentSafetyError("office archive is corrupted")
        except zipfile.BadZipFile as exc:
            raise DocumentSafetyError("office document is not a valid ZIP package") from exc
