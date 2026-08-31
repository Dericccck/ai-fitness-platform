"""在已配置的评估数据库上启动可选的 TruLens 仪表盘。"""

from __future__ import annotations

import argparse

from app.core.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--address", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()
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
