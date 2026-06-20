ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=control_plane
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
ENV PYTHONPATH=/app
COPY services/ /app/services/
EXPOSE 8008
ENV PYTHONUNBUFFERED=1

# Control plane-specific: Docker/Kubernetes management tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI for container management
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz | \
    tar xz --strip-components=1 -C /usr/local/bin docker/docker

# Install docker-compose standalone
RUN wget -q https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 -O /usr/local/bin/docker-compose && \
    chmod +x /usr/local/bin/docker-compose

# Install kubectl for Kubernetes management
RUN curl -fsSL https://dl.k8s.io/release/v1.31.0/bin/linux/amd64/kubectl -o /usr/local/bin/kubectl && \
    chmod +x /usr/local/bin/kubectl

CMD ["uvicorn", "services.control_plane.main:app", "--host", "0.0.0.0", "--port", "8008"]
