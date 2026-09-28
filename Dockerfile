# API 与 Worker 共用的运行时镜像（compose 分别覆盖 command）
FROM ghcr.io/astral-sh/uv:0.11.6 AS uv

FROM python:3.11-slim-bookworm

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# 先装第三方依赖（仅锁文件变化时重建该层）；再装项目本体
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY apps ./apps
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev --no-editable

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# uvicorn access log 由结构化请求日志替代（带 trace_id），故关闭
CMD ["/app/.venv/bin/uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
