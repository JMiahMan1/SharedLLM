FROM python:3.11-slim
WORKDIR /app
# Install system deps required for sentence-transformers
RUN apt-get update && apt-get install -y build-essential curl && rm -rf /var/lib/apt/lists/*
COPY services/gateway/requirements.txt ./requirements.txt
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"
COPY services/gateway/. .
COPY docs ./docs
COPY README.md EXAMPLES.md TESTING.md ./

# --- Privilege Drop: create dedicated sharedllm user ---
ARG PUID=1000
ARG PGID=1000
RUN groupadd -g ${PGID} sharedllm || true \
    && useradd -u ${PUID} -g ${PGID} -m -s /bin/bash sharedllm || true \
    && chown -R ${PUID}:${PGID} /app \
    && mkdir -p /home/sharedllm/.cache/huggingface /home/sharedllm/.cache/qdrant \
    && chown -R ${PUID}:${PGID} /home/sharedllm
ENV HOME=/home/sharedllm

EXPOSE 8002
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "11435"]
