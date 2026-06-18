FROM ${BASE_IMAGE:-ghcr.io/jmiahman1/sharedllm-base:latest} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=storage
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
COPY . .
EXPOSE 8005
CMD ["uvicorn", "services.storage.main:app", "--host", "0.0.0.0", "--port", "8005"]
