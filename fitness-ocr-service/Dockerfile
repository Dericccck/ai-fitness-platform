# OCR 模型运行环境面向 Linux 部署节点。由于 Paddle 安装包具有平台差异，
# 本地开发默认不安装推理 extra。
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PADDLE_HOME=/models/paddle \
    PATH="/service/.venv/bin:${PATH}"
WORKDIR /service

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra inference --no-install-project

COPY app ./app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra inference

RUN groupadd --gid 10001 ocr \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin ocr \
    && mkdir -p /models/paddle \
    && chown -R ocr:ocr /service /models
USER 10001:10001

EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/health/live', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8091"]
