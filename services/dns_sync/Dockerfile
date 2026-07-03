ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=dns_sync
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
COPY . .
ENV PYTHONUNBUFFERED=1

# DNS sync-specific: DNS/network tools (dnsutils already provides dig, host, nslookup)
RUN apt-get update && apt-get install -y --no-install-recommends \
    dnsutils \
    net-tools \
    iputils-ping \
    traceroute \
    nmap \
    python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir docker

CMD ["python", "services/dns_sync/config/dns_sync.py"]
