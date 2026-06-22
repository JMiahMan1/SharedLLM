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
COPY services/__init__.py /app/services/
COPY services/config.py /app/services/
COPY services/shared/ /app/services/shared/
COPY services/execution/ /app/services/execution/
EXPOSE 8003
ENV PYTHONUNBUFFERED=1

# Execution-specific: media processing, packet capture, device discovery tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tshark \
    snmp \
    arp-scan \
    && rm -rf /var/lib/apt/lists/*

CMD ["uvicorn", "services.execution.main:app", "--host", "0.0.0.0", "--port", "8003"]
