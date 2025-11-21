# Dockerfile
# Use official Python slim image
FROM pytorch/pytorch:2.9.0-cuda12.8-cudnn9-runtime

# Set working directory
WORKDIR /app

# Copy requirements first to leverage caching
COPY requirements.txt .

# Install dependencies
RUN apt update && apt install -y curl iputils-ping tesseract-ocr poppler-utils ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app and data
COPY app/ ./app
COPY data/ ./data

# Expose FastAPI port
EXPOSE 11435

# Set Python unbuffered for real-time logging
ENV PYTHONUNBUFFERED=1

# Default command to start FastAPI app
CMD ["python", "app/main.py"]
