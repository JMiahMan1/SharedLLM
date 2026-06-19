FROM python:3.11-slim
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=gateway
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
# Install CPU-only PyTorch (no CUDA libs)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ffmpeg \
    iputils-ping \
    ripgrep \
    unzip \
    snmp \
    arp-scan \
    iproute2 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    tzdata \
    && rm -rf /var/lib/apt/lists/*
# Install deno
RUN wget -q https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -O /tmp/deno.zip && unzip -o /tmp/deno.zip -d /usr/local/bin && chmod +x /usr/local/bin/deno && rm /tmp/deno.zip
# --- Privilege Drop: create dedicated sharedllm user ---
ARG PUID=1000
ARG PGID=1000
RUN groupadd -g ${PGID} sharedllm || true \
    && useradd -u ${PUID} -g ${PGID} -m -s /bin/bash sharedllm || true \
    && chown -R ${PUID}:${PGID} /app \
    && mkdir -p /home/sharedllm/.cache/huggingface /home/sharedllm/.cache/qdrant \
    && chown -R ${PUID}:${PGID} /home/sharedllm
ENV HOME=/home/sharedllm
COPY services/gateway/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY services/gateway/ /app/
EXPOSE 11435
ENV PYTHONUNBUFFERED=1
ENV TOKENIZERS_PARALLELISM=false
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "11435"]
