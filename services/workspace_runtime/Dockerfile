ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=workspace_runtime
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
ENV PYTHONPATH=/app
RUN mkdir -p /home/sharedllm && chown 1000:1000 /home/sharedllm
COPY services/ /app/services/
EXPOSE 8007
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Workspace-specific: ALL coding tools agentic AIs use
# fd alias (base image already provides fd-find at /usr/bin/fd-find)
RUN update-alternatives --install /usr/bin/fd fd /usr/bin/fd-find 100 2>/dev/null || true

# Install Node.js for frontend/tooling support (npm packages)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g npm@latest

# Install coding-related npm packages that agentic AIs use
RUN npm install -g \
    typescript \
    typescript-language-server \
    vscode-langservers-extracted \
    shfmt \
    shellcheck

# Install Docker CLI for container management
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz | \
    tar xz --strip-components=1 -C /usr/local/bin docker/docker

# Install deno
RUN wget -q https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -O /tmp/deno.zip && \
    unzip -o /tmp/deno.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/deno && \
    rm /tmp/deno.zip

# Install python language servers
RUN pip install --no-cache-dir \
    pyright \
    pylint \
    flake8 \
    black \
    isort \
    autopep8 \
    mypy \
    jedi \
    python-lsp-server \
    rope \
    yappi \
    memory-profiler \
    line-profiler

CMD ["uvicorn", "services.workspace_runtime.main:app", "--host", "0.0.0.0", "--port", "8007"]
