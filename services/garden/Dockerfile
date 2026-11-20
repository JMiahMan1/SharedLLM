ARG BASE_IMAGE=ghcr.io/jmiahman1/sharedllm-base:latest
FROM ${BASE_IMAGE} AS base
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG SERVICE_NAME=garden
ENV GIT_SHA=${GIT_SHA}
ENV BUILD_DATE=${BUILD_DATE}
ENV SERVICE_NAME=${SERVICE_NAME}
WORKDIR /app
ENV PYTHONPATH=/app
COPY services/garden/requirements.txt /tmp/garden-requirements.txt
RUN pip install --no-cache-dir -r /tmp/garden-requirements.txt
COPY . .
RUN python3 scripts/build_plants_seed.py --work-dir /tmp/plants-build --out /app/plants-seed.db && rm -rf /tmp/plants-build
EXPOSE 8012
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "services.garden.main:app", "--host", "0.0.0.0", "--port", "8012"]
