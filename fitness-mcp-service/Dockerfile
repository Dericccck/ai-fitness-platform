FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
WORKDIR /workspace/fitness-mcp-service
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
COPY packages/fitness-tool-contracts /workspace/packages/fitness-tool-contracts
COPY fitness-mcp-service/pyproject.toml fitness-mcp-service/uv.lock ./
COPY fitness-mcp-service/app ./app
RUN uv sync --frozen --no-dev --no-editable
RUN useradd --system --uid 10001 --no-create-home appuser
USER 10001
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health/live',timeout=2)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--timeout-graceful-shutdown", "15"]
