# 构建阶段包含 uv 和完整依赖解析能力；最终运行镜像不保留构建工具与开发依赖。
FROM python:3.11-slim-bookworm AS builder

# uv 官方建议从固定版本的 distroless 镜像复制二进制，避免执行网络安装脚本。
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /service

# 先复制依赖描述，业务代码变化时仍可复用依赖安装层缓存。
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/service/.venv/bin:${PATH}"
WORKDIR /service

# 固定非 root UID/GID，便于 Kubernetes securityContext 和挂载卷权限保持一致。
RUN groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin agent

COPY --from=builder --chown=agent:agent /service /service

USER 10001:10001
EXPOSE 8090

# 使用 Python 标准库执行存活检查，避免仅为 HEALTHCHECK 在运行镜像安装 curl。
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health/live', timeout=2)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
