FROM python:3.11-slim
RUN apt-get update && apt-get install -y git curl jq ripgrep fd-find tree docker.io docker-compose libsndfile1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# --- Privilege Drop: create dedicated sharedllm user (UID/GID 1000) ---
RUN groupadd -g 1000 sharedllm || true \
    && useradd -u 1000 -g 1000 -m -s /bin/bash sharedllm || true \
    && chown -R 1000:1000 /app \
    && mkdir -p /home/sharedllm/.ssh /home/sharedllm/.cache \
    && chown -R 1000:1000 /home/sharedllm
ENV HOME=/home/sharedllm

EXPOSE 8003
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
