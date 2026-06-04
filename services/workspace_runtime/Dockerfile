FROM python:3.11-slim
WORKDIR /app
RUN mkdir -p /home/sharedllm && chown 1000:1000 /home/sharedllm
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
    nodejs \
    npm \
    golang \
    yamllint \
    shellcheck \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN pip install --no-cache-dir -r services/workspace_runtime/requirements.txt \
    && pip install --no-cache-dir flake8 black mypy
ENV PYTHONPATH=/app
EXPOSE 8007
CMD ["uvicorn", "services.workspace_runtime.main:app", "--host", "0.0.0.0", "--port", "8007"]
