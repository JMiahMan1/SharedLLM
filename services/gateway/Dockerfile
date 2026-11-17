ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=gateway
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
ENV PYTHONPATH=/app
# --- Privilege Drop: create dedicated sharedllm user ---
ARG PUID=1000
ARG PGID=1000
RUN groupadd -g ${PGID} sharedllm || true \
    && useradd -u ${PUID} -g ${PGID} -m -s /bin/bash sharedllm || true \
    && chown -R ${PUID}:${PGID} /app \
    && mkdir -p /home/sharedllm/.cache/huggingface /home/sharedllm/.cache/qdrant \
    && chown -R ${PUID}:${PGID} /home/sharedllm
ENV HOME=/home/sharedllm
COPY . .
EXPOSE 11435
ENV PYTHONUNBUFFERED=1
ENV TOKENIZERS_PARALLELISM=false

# Gateway-specific: network debugging for LLM/media routing
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    jq \
    && rm -rf /var/lib/apt/lists/*

# A deploy stops this container with requests in flight. The push stream and
# the chat long polls never finish on their own, so uvicorn used to wait for
# them until Docker killed it 10 s later, and only then could the new gateway
# start: every phone's requests hung for 15-25 s. Those streams reconnect by
# design, so 3 s is enough for ordinary requests to finish.
CMD ["uvicorn", "services.gateway.main:app", "--host", "0.0.0.0", "--port", "11435", "--timeout-graceful-shutdown", "3"]
