ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=rag
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
COPY services/__init__.py /app/services/
COPY services/config.py /app/services/
COPY services/shared/ /app/services/shared/
COPY services/rag/ /app/services/rag/
ENV PYTHONPATH=/app
EXPOSE 8004
ENV PYTHONUNBUFFERED=1
ENV TOKENIZERS_PARALLELISM=false

# RAG-specific: vector/embedding debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
    jq \
    && rm -rf /var/lib/apt/lists/*

CMD ["uvicorn", "services.rag.main:app", "--host", "0.0.0.0", "--port", "8004"]
