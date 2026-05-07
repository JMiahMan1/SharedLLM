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
EXPOSE 8002
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "11435"]
