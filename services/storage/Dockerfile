ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=storage
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
ENV PYTHONPATH=/app
COPY . .
EXPOSE 8005
ENV PYTHONUNBUFFERED=1

# PDF books: reading a Calibre PDF (chapters, fetch_text) and indexing it need pypdf. The
# image never installs requirements.txt (only CI does), so without this every PDF book
# failed with "No module named 'pypdf'". The import check fails the build, not the reads.
RUN pip install --no-cache-dir "pypdf==6.21.4" && python -c "import pypdf"

# Storage-specific: WebDAV/network debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    jq \
    && rm -rf /var/lib/apt/lists/*

CMD ["uvicorn", "services.storage.main:app", "--host", "0.0.0.0", "--port", "8005"]
