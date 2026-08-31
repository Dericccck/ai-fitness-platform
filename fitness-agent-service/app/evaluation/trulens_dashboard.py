"""在已配置的评估数据库上启动可选的 TruLens 仪表盘。"""

from __future__ import annotations

import argparse
import ipaddress

from app.core.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--address", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
    try:
        is_loopback = ipaddress.ip_address(args.address).is_loopback
    except ValueError:
        is_loopback = args.address.lower() == "localhost"
    if not is_loopback:
        raise SystemExit(
            "TruLens 仪表盘只允许绑定回环地址；如需远程访问，请通过带身份认证的反向代理转发"
        )
    from trulens.core import TruSession
    from trulens.core.database.connector.default import DefaultDBConnector

    settings = get_settings()
    session = TruSession(
        connector=DefaultDBConnector(
            database_url=args.database_url or settings.trulens_database_url
        )
    )
    session.run_dashboard(address=args.address, port=args.port)


if __name__ == "__main__":
    main()
