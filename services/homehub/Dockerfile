ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
WORKDIR /app
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
# ffmpeg turns motion clips from cameras that cannot record themselves into MP4
# (services/homehub/camera_recording.py).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# Device libraries first: they change rarely, so CI reuses this layer and a
# code change ships as a small source layer.
COPY services/homehub/requirements.txt /tmp/homehub-requirements.txt
RUN pip install --no-cache-dir -r /tmp/homehub-requirements.txt
# The per-commit build args and the source copy come last (see the execution
# Dockerfile for why: anything above them is rebuilt on every commit).
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=homehub
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
COPY . .
EXPOSE 8123
# As a module, not a script: a script puts services/homehub/ first on sys.path,
# where a module named like a library one (statistics.py) shadowed it.
CMD ["python", "-m", "services.homehub.main"]
