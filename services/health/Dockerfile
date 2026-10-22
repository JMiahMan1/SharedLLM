ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=health
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
ENV PYTHONPATH=/app
COPY services/health/requirements.txt /tmp/health-requirements.txt
RUN pip install --no-cache-dir -r /tmp/health-requirements.txt
COPY . .
EXPOSE 8011
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "services.health.main:app", "--host", "0.0.0.0", "--port", "8011"]
