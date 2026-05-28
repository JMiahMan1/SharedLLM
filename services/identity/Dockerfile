FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
COPY . .
RUN pip install --no-cache-dir -r services/identity/requirements.txt
ENV PYTHONPATH=/app
EXPOSE 8001
CMD ["uvicorn", "services.identity.main:app", "--host", "0.0.0.0", "--port", "8001"]
