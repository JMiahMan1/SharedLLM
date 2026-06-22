ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=logging
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
ENV PYTHONPATH=/app
COPY . .
EXPOSE 8006
ENV PYTHONUNBUFFERED=1

# Logging-specific: websocket/network debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
    jq \
    && rm -rf /var/lib/apt/lists/*

CMD ["uvicorn", "services.logging.main:app", "--host", "0.0.0.0", "--port", "8006"]
