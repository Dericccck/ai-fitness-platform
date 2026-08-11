import logging
import sys

import structlog


def configure_logging(log_level: str) -> None:
    """配置 Agent 服务统一的 JSON 结构化日志。

    Agent 请求会跨越 HTTP、模型、RAG、工具和 Java 后端。纯文本日志很难按一次请求
    聚合，因此这里统一输出 JSON，并通过 structlog contextvars 自动合并 request_id、
    trace_id 等上下文字段。日志只记录定位问题所需的元数据；Prompt、模型原文和工具
    敏感参数必须经过专门的脱敏与审计策略后才能记录。

    Args:
        log_level: 环境配置的日志级别，例如 INFO 或 DEBUG。非法值安全回退为 INFO。
    """

    normalized_level = getattr(logging, log_level.upper(), logging.INFO)

    # 保留标准库日志的基础配置，确保第三方库即使没有使用 structlog 也能输出。
    logging.basicConfig(
        level=normalized_level,
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )

    structlog.configure(
        processors=[
            # 将请求中间件绑定到 contextvars 的字段自动加入当前协程的每条日志。
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(normalized_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
