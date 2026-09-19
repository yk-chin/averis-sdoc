# shipdoc API —— Cloud Run
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 只拷代码；data/ .env .cache/ evals/ 由 .dockerignore / .gcloudignore 排除
COPY shipdoc_core/ shipdoc_core/
COPY pipeline/ pipeline/
COPY api/ api/

# 非 root 运行；.cache 是 LLM 结果缓存目录（容器内临时，可写）
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/.cache && chown -R app:app /app
USER app

ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
