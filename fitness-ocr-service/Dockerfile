# OCR 模型运行环境面向 Linux 部署节点。由于 Paddle 安装包具有平台差异，
# 本地开发默认不安装推理 extra。
# 固定基础镜像 manifest，避免构建时因多架构 tag 漂移导致不可复现或摘要冲突。
FROM python@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PADDLE_HOME=/models/paddle \
    PATH="/service/.venv/bin:${PATH}"
WORKDIR /service

# uv 版本同样固定到 manifest digest，保证依赖解析工具不随 tag 漂移。
COPY --from=ghcr.io/astral-sh/uv@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /uvx /bin/
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra inference --no-install-project

COPY app ./app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra inference

# OpenCV 在 CPU 推理环境仍需要这些运行时动态库；放在 Python 依赖层之后，避免依赖缓存失效。
# 默认使用清华 Debian 镜像以适配本地网络；生产构建可通过 DEBIAN_MIRROR 覆盖为企业镜像。
ARG DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn
RUN sed -i "s|http://deb.debian.org/debian-security|${DEBIAN_MIRROR}/debian-security|g; s|http://deb.debian.org/debian|${DEBIAN_MIRROR}/debian|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get -o Acquire::Retries=5 install --no-install-recommends --yes libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 依赖安装完成后再设置非 root 运行用户的 HOME，避免修改运行时环境时让依赖层缓存失效。
ENV HOME=/home/ocr

RUN groupadd --gid 10001 ocr \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin ocr \
    && mkdir -p /home/ocr /models/paddle \
    && chown -R ocr:ocr /home/ocr /service /models
USER 10001:10001

EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/health/live', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8091"]
