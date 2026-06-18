ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=execution
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
# Execution-specific: install deno + extra system tools
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep unzip snmp arp-scan iproute2 && rm -rf /var/lib/apt/lists/*
RUN wget -q https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -O /tmp/deno.zip && unzip -o /tmp/deno.zip -d /usr/local/bin && chmod +x /usr/local/bin/deno && rm /tmp/deno.zip
COPY . .
EXPOSE 8003
CMD ["uvicorn", "services.execution.main:app", "--host", "0.0.0.0", "--port", "8003"]
