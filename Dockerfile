# Dockerfile
# Use official Python slim image
FROM pytorch/pytorch:2.9.0-cuda12.8-cudnn9-runtime

# Set working directory
WORKDIR /app

# Copy requirements first to leverage caching
COPY requirements.txt .

# Install dependencies
RUN apt-get update && apt-get install --fix-missing --no-install-recommends -y \
    curl \
    iputils-ping \
    tesseract-ocr \
    poppler-utils \
    ffmpeg \
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
