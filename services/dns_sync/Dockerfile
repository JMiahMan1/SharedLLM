FROM python:3.11-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .
ENV PYTHONPATH=/app

CMD ["python", "config/dns_sync.py"]
