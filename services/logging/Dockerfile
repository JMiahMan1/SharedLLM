FROM sharedllm-base:latest AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=logging
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
COPY . .
EXPOSE 8006
CMD ["uvicorn", "services.logging.main:app", "--host", "0.0.0.0", "--port", "8006"]
