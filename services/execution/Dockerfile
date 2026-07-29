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
COPY . .
EXPOSE 8003
ENV PYTHONUNBUFFERED=1

# Execution-specific: media processing, packet capture, device discovery tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tshark \
    snmp \
    arp-scan \
    curl \
    ca-certificates \
    jq \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI (gh) — used by the gh workspace tool (repo create, PRs, issues).
# Authentication is injected at runtime via the GH_TOKEN env (no credential store).
RUN set -eux; \
    ARCH="$(uname -m)"; \
    if [ "$ARCH" = "x86_64" ]; then GH_ARCH="amd64"; else GH_ARCH="$ARCH"; fi; \
    GH_VERSION="$(curl -fsSL https://api.github.com/repos/cli/cli/releases/latest | jq -r .tag_name)"; \
    curl -fsSL "https://github.com/cli/cli/releases/download/${GH_VERSION}/gh_${GH_VERSION#v}_linux_${GH_ARCH}.tar.gz" -o /tmp/gh.tar.gz; \
    tar -xzf /tmp/gh.tar.gz -C /tmp; \
    mv "/tmp/gh_${GH_VERSION#v}_linux_${GH_ARCH}/bin/gh" /usr/local/bin/gh; \
    rm -rf /tmp/gh.tar.gz "/tmp/gh_${GH_VERSION#v}_linux_${GH_ARCH}"; \
    gh --version

# Install Playwright Chromium browser and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-liberation \
    fonts-noto-cjk \
    libglib2.0-0 \
    libnss3 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0.0 \
    libcairo2 \
    libasound2 \
    libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*
RUN playwright install chromium

# Install camoufox browser for anti-bot evasion
RUN pip install --no-cache-dir camoufox
ENV XDG_CACHE_HOME=/home/sharedllm/.cache
RUN mkdir -p /home/sharedllm/.cache/camoufox && touch /home/sharedllm/.cache/camoufox/.0.5_FLAG && python -m camoufox sync && python -m camoufox fetch

CMD ["uvicorn", "services.execution.main:app", "--host", "0.0.0.0", "--port", "8003"]
