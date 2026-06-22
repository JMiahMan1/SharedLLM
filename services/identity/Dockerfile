ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=identity
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
ENV PYTHONPATH=/app
COPY services/__init__.py /app/services/
COPY services/config.py /app/services/
COPY services/shared/ /app/services/shared/
COPY services/identity/ /app/services/identity/
EXPOSE 8001
ENV PYTHONUNBUFFERED=1

# Identity-specific: JWT/token debugging tools and SQLite
RUN apt-get update && apt-get install -y --no-install-recommends \
    jq \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

CMD ["uvicorn", "services.identity.main:app", "--host", "0.0.0.0", "--port", "8001"]
