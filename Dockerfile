# Dockerfile
# Use official Python slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install CPU-only PyTorch (no CUDA libs)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Copy requirements first to leverage caching
COPY requirements.txt .

# Install dependencies
RUN apt-get update && apt-get install --fix-missing --no-install-recommends -y \
    curl \
    gnupg \
    ffmpeg \
    iputils-ping \
    tesseract-ocr \
    poppler-utils \
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

RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . .
ENV PYTHONPATH=/app

# Expose FastAPI port
EXPOSE 11435

# Set Python unbuffered for real-time logging
ENV PYTHONUNBUFFERED=1
ENV TOKENIZERS_PARALLELISM=false

# Default command to start FastAPI app
CMD ["python", "app/main.py"]
