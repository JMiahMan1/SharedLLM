FROM ghcr.io/jmiahman1/sharedllm-base:latest

EXPOSE 8009
WORKDIR /home/sharedllm

CMD ["uvicorn", "services.geo.main:app", "--host", "0.0.0.0", "--port", "8009"]
