FROM sharedllm-base:latest AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=workspace_runtime
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
RUN mkdir -p /home/sharedllm && chown 1000:1000 /home/sharedllm
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir flake8 black mypy
ENV PYTHONPATH=/app
EXPOSE 8007
CMD ["uvicorn", "services.workspace_runtime.main:app", "--host", "0.0.0.0", "--port", "8007"]
