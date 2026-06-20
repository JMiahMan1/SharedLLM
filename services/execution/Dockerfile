ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=execution
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
ENV PYTHONPATH=/app
COPY services/ /app/services/
COPY services/execution/ /app/
EXPOSE 8003
ENV PYTHONUNBUFFERED=1

# Execution-specific: media processing, packet capture, device discovery tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tshark \
    snmp \
    arp-scan \
    && rm -rf /var/lib/apt/lists/*

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
