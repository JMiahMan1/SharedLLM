ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=rag
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
COPY . .
ENV PYTHONPATH=/app
EXPOSE 8004
ENV PYTHONUNBUFFERED=1
ENV TOKENIZERS_PARALLELISM=false

# RAG-specific: vector/embedding debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
    jq \
    && rm -rf /var/lib/apt/lists/*

# sqlite-vec provides the native vector-search extension (prebuilt wheel, no cargo needed).
# numpy is required by the pure-Python fallback adapter.
RUN pip install --no-cache-dir sqlite-vec numpy

CMD ["uvicorn", "services.rag.main:app", "--host", "0.0.0.0", "--port", "8004"]
