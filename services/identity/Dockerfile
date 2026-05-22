FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
COPY services/identity/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/identity/ ./
COPY services/config.py ./config.py
EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
