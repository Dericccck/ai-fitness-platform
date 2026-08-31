"""针对已配置 ClamAV 适配器的本地实时安全冒烟测试。"""

from __future__ import annotations

import sys

from app.core.config import get_settings

from .safety import ClamAvScanner, DocumentSafetyError

EICAR_TEST_STRING = r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def main() -> int:
    """连接真实 ClamAV 守护进程，验证正常文件通过、EICAR 测试串被拒绝。"""

    settings = get_settings()
    if settings.rag_malware_scanner_backend != "clamav":
        print("AGENT_RAG_MALWARE_SCANNER_BACKEND 必须配置为 clamav")
        return 2
    scanner = ClamAvScanner(
        settings.rag_clamav_host,
        port=settings.rag_clamav_port,
        timeout_seconds=settings.rag_clamav_timeout_seconds,
    )
    clean = scanner.scan("security-check.txt", b"fitness-agent security check")
    if clean.status != "CLEAN":
        print(f"正常文件返回了异常 verdict：{clean.status}")
        return 1
    try:
        scanner.scan("eicar.com", EICAR_TEST_STRING.encode("ascii"))
    except DocumentSafetyError as exc:
        if "检测到恶意软件" not in str(exc):
            print(f"EICAR 测试串返回了异常错误：{exc}")
            return 1
    else:
        print("ClamAV 没有拒绝 EICAR 测试签名")
        return 1
    print("ClamAV 实时安全检查通过：正常文件已接受，EICAR 测试串已拒绝")
    return 0


if __name__ == "__main__":
    sys.exit(main())
