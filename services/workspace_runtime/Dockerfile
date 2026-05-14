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

# --- Privilege Drop: create dedicated sharedllm user (UID/GID 1000) ---
RUN groupadd -g 1000 sharedllm || true \
    && useradd -u 1000 -g 1000 -m -s /bin/bash sharedllm || true \
    && chown -R 1000:1000 /app \
    && mkdir -p /home/sharedllm/.cache /home/sharedllm/.config \
    && chown -R 1000:1000 /home/sharedllm
ENV HOME=/home/sharedllm

EXPOSE 8007
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8007"]
