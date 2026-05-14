FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y \
    git \
    curl \
    patch \
    jq \
    wget \
    procps \
    iputils-ping \
    net-tools \
    vim-tiny \
    nodejs \
    npm \
    golang \
    yamllint \
    shellcheck \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir flake8 black mypy
COPY . .

# --- Privilege Drop: create dedicated sharedllm user ---
ARG PUID=1000
ARG PGID=1000
RUN groupadd -g ${PGID} sharedllm || true \
    && useradd -u ${PUID} -g ${PGID} -m -s /bin/bash sharedllm || true \
    && chown -R ${PUID}:${PGID} /app \
    && mkdir -p /home/sharedllm/.cache /home/sharedllm/.config \
    && chown -R ${PUID}:${PGID} /home/sharedllm
ENV HOME=/home/sharedllm

EXPOSE 8007
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8007"]
