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
    libnss-mdns \
    && rm -rf /var/lib/apt/lists/*
# mDNS (.local) name resolution for host-networked services (vision OCR proxy
# at jeremiah-home-desktop.local:11434 etc.) — mirror the host's nsswitch so
# .local names resolve the same way the host does.
RUN sed -i 's/^hosts:.*/hosts:          files mdns4_minimal [NOTFOUND=return] dns/' /etc/nsswitch.conf

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
    libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*
# Install camoufox browser for anti-bot evasion FIRST — it pins a specific
# playwright version, so any playwright install must come after it.
RUN pip install --no-cache-dir camoufox
ENV XDG_CACHE_HOME=/home/sharedllm/.cache
# Build camoufox browser cache at build time, then make it world-writable so uid 1000 can create files at runtime
RUN mkdir -p /home/sharedllm/.cache/camoufox && touch /home/sharedllm/.cache/camoufox/.0.5_FLAG && python -m camoufox sync && python -m camoufox fetch && chmod -R a+rwX /home/sharedllm/.cache/camoufox

# Route DNS through the gateway's dnsmasq so .local hostnames (synced by the
# DNS sync service) resolve inside this host-networked container — mDNS
# (libnss-mdns) cannot multicast in a container without NET_ADMIN, and the
# host systemd-resolved stub (127.0.0.53) never forwards .local upstream.
# Note: resolv.conf is a bind-mount at build time, so overwrite in place
# (a rename-based sed -i fails with "Device or resource busy").
RUN printf 'nameserver 192.168.2.1\n' > /etc/resolv.conf && cat /etc/resolv.conf

# Install Playwright Chromium LAST (after camoufox), so the downloaded browser
# revision matches the final playwright version at runtime. Installed into the
# runtime HOME cache (/home/sharedllm) so uid 1000 (PUID:PGID, HOME=/home/sharedllm)
# can find it, then made world-writable so the runtime user can manage it.
ENV HOME=/home/sharedllm
RUN mkdir -p /home/sharedllm/.cache && playwright install chromium && chmod -R a+rwX /home/sharedllm/.cache/ms-playwright

# Ensure the runtime HOME dir is owned by the runtime user (uid 1000). Without
# this, docker auto-creates the /home/sharedllm volume-mount point as root:755,
# and camoufox/firefox hang at startup (~180s) failing to create ~/.camoufox
# and the fontconfig cache.
RUN mkdir -p /home/sharedllm && chown -R 1000:1000 /home/sharedllm && chmod -R a+rwX /home/sharedllm

CMD ["uvicorn", "services.execution.main:app", "--host", "0.0.0.0", "--port", "8003"]
