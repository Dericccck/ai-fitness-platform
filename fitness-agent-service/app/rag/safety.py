"""文档进入审核或对象存储前的结构安全检查。"""

from __future__ import annotations

import hashlib
import io
import socket
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal, Protocol

StructuralSafetyStatus = Literal["STRUCTURAL_VALIDATED", "CLEAN"]
# NOT_CONFIGURED 表示未接入外部杀毒服务，不能被解释为安全；CLEAN 才是外部扫描通过。
MalwareStatus = Literal["NOT_CONFIGURED", "CLEAN"]


class DocumentSafetyError(ValueError):
    """上传文件格式错误、不安全或超过压缩包展开限制。"""


class DocumentSecurityUnavailable(DocumentSafetyError):
    """已配置的外部恶意软件服务无法提供安全 verdict。"""


@dataclass(frozen=True)
class SafetyScanResult:
    """文件进入暂存区前执行确定性检查后形成的可审计结果。"""

    sha256: str
    status: StructuralSafetyStatus
    scanner_name: str
    malware_status: MalwareStatus = "NOT_CONFIGURED"
    malware_scanner: str = "not-configured"
    malware_signature: str | None = None
    malware_scanned_at: datetime | None = None


@dataclass(frozen=True)
class MalwareScanResult:
    """与确定性结构检查分开保存的外部恶意软件 verdict。"""

    status: MalwareStatus
    scanner_name: str
    signature: str | None = None
    scanned_at: datetime | None = None


class DocumentScanner(Protocol):
    """管理员上传流程使用的组合式文件安全扫描契约。"""

    def scan(self, file_name: str, content: bytes) -> SafetyScanResult:
        """返回可审计的结构检查和恶意软件扫描结果。"""


class MalwareScanner(Protocol):
    """对象存储前调用的同步恶意软件扫描边界。"""

    def scan(self, file_name: str, content: bytes) -> MalwareScanResult:
        """返回 clean verdict；扫描异常时执行 fail-closed 安全策略。"""


class StructuralDocumentScanner:
    """在解析或存储前拒绝明显损坏的文件和 ZIP 炸弹。

    该扫描器不是杀毒引擎。生产环境应在它外层接入 ClamAV 或云端恶意软件服务，
    并单独持久化外部扫描 verdict。
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
    """通过 ClamAV 守护进程的 ``INSTREAM`` 协议扫描字节内容。

    适配器不会把上传文件写入临时路径，而是通过私有 TCP 连接分块传输。
    ClamAV 不可用时执行 fail-closed，避免生产环境因扫描服务异常而静默关闭恶意软件防护。
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
        """将一次内存中的上传内容发送给 ClamAV，并解析稳定的流式响应。"""

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
    """依次执行确定性检查和外部恶意软件扫描。"""

    def __init__(
        self,
        structural_scanner: StructuralDocumentScanner,
        malware_scanner: MalwareScanner | None = None,
    ) -> None:
        self.structural_scanner = structural_scanner
        self.malware_scanner = malware_scanner

    def scan(self, file_name: str, content: bytes) -> SafetyScanResult:
        """保留结构检查身份，同时附加外部安全 verdict。"""

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
