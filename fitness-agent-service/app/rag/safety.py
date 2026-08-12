"""Structural safety checks before a document enters review or object storage."""

from __future__ import annotations

import hashlib
import io
import socket
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Protocol


class DocumentSafetyError(ValueError):
    """The upload is malformed, unsafe, or violates archive expansion limits."""


class DocumentSecurityUnavailable(DocumentSafetyError):
    """The configured external malware service cannot provide a safe verdict."""


@dataclass(frozen=True)
class SafetyScanResult:
    """Auditable result of deterministic checks performed before staging."""

    sha256: str
    status: str
    scanner_name: str
    malware_status: str = "NOT_CONFIGURED"
    malware_scanner: str = "not-configured"
    malware_signature: str | None = None
    malware_scanned_at: datetime | None = None


@dataclass(frozen=True)
class MalwareScanResult:
    """External malware verdict stored separately from deterministic structure checks."""

    status: str
    scanner_name: str
    signature: str | None = None
    scanned_at: datetime | None = None


class DocumentScanner(Protocol):
    """Combined upload safety scanner contract used by the admin workflow."""

    def scan(self, file_name: str, content: bytes) -> SafetyScanResult:
        """Return auditable structural and malware results."""


class MalwareScanner(Protocol):
    """Synchronous malware scanning boundary called before object storage."""

    def scan(self, file_name: str, content: bytes) -> MalwareScanResult:
        """Return a clean verdict or raise a fail-closed safety error."""


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


class ClamAvScanner:
    """Scan bytes through ClamAV's daemon ``INSTREAM`` protocol.

    The adapter does not write uploads to a temporary path. It streams bounded chunks
    over a private TCP connection and fails closed when ClamAV is unavailable, so a
    production deployment cannot silently turn off malware protection because the
    scanning service is unhealthy.
    """

    def __init__(
        self,
        host: str,
        port: int = 3310,
        timeout_seconds: float = 10.0,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.chunk_size = chunk_size

    def scan(self, file_name: str, content: bytes) -> MalwareScanResult:
        """Send one in-memory upload to ClamAV and parse its stable stream response."""

        try:
            with socket.create_connection(
                (self.host, self.port), timeout=self.timeout_seconds
            ) as connection:
                connection.sendall(b"zINSTREAM\0")
                for start in range(0, len(content), self.chunk_size):
                    chunk = content[start : start + self.chunk_size]
                    connection.sendall(len(chunk).to_bytes(4, "big"))
                    connection.sendall(chunk)
                connection.sendall((0).to_bytes(4, "big"))
                # ClamAV 的 INSTREAM 响应通常以 NUL 字节结束（例如
                # ``stream: OK\\0``），不能只调用 ``strip()``，因为它不会移除 NUL。
                # 这里仅清理协议允许的边界字符，保留正文，避免把真实 verdict 截断。
                response = (
                    connection.recv(4096).decode("utf-8", errors="replace").strip("\x00\r\n ")
                )
        except OSError as exc:
            raise DocumentSecurityUnavailable("malware scanner is unavailable") from exc

        if not response.startswith("stream:"):
            raise DocumentSafetyError("malware scanner returned an invalid response")
        verdict = response.removeprefix("stream:").strip()
        if verdict.endswith(" FOUND"):
            signature = verdict.removesuffix(" FOUND").strip() or None
            raise DocumentSafetyError(f"malware detected: {signature or 'unknown signature'}")
        if verdict != "OK":
            raise DocumentSafetyError("malware scanner returned an unsafe verdict")
        return MalwareScanResult(
            status="CLEAN",
            scanner_name="clamav-instream",
            scanned_at=datetime.now(UTC),
        )


class CompositeDocumentScanner:
    """Run deterministic checks and an external malware scanner in sequence."""

    def __init__(
        self,
        structural_scanner: StructuralDocumentScanner,
        malware_scanner: MalwareScanner | None = None,
    ) -> None:
        self.structural_scanner = structural_scanner
        self.malware_scanner = malware_scanner

    def scan(self, file_name: str, content: bytes) -> SafetyScanResult:
        """Preserve the structural identity while adding the external security verdict."""

        structural = self.structural_scanner.scan(file_name, content)
        if self.malware_scanner is None:
            return structural
        malware = self.malware_scanner.scan(file_name, content)
        return SafetyScanResult(
            sha256=structural.sha256,
            status="CLEAN",
            scanner_name=f"{structural.scanner_name}+{malware.scanner_name}",
            malware_status=malware.status,
            malware_scanner=malware.scanner_name,
            malware_signature=malware.signature,
            malware_scanned_at=malware.scanned_at,
        )
