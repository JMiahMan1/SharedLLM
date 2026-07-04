FROM ghcr.io/jmiahman1/sharedllm-base:latest

RUN pip install --no-cache-dir docker aiohttp

WORKDIR /app

COPY services/dns_service/main.py .

CMD ["python", "main.py"]
