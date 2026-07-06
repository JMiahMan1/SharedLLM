ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
WORKDIR /app
ENV PYTHONPATH=/app
RUN pip install --no-cache-dir docker aiohttp
ENV PYTHONUNBUFFERED=1
COPY . .
CMD ["python", "services/dns/main.py"]
