FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y \
    git \
    curl \
    patch \
    jq \
    wget \
    procps \
    iputils-ping \
    net-tools \
    vim-tiny \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8007
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8007"]
