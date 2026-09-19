# ShipDoc API - Cloud Run
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# code only; data/ .env .cache/ evals/ are excluded by .dockerignore / .gcloudignore
COPY shipdoc_core/ shipdoc_core/
COPY pipeline/ pipeline/
COPY api/ api/

# run as non-root; .cache is the LLM result cache (ephemeral, writable inside the container)
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/.cache && chown -R app:app /app
USER app

ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
