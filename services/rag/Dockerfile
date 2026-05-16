FROM python:3.11-slim
WORKDIR /app
COPY services/rag/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/rag/ ./
COPY services/config.py ./config.py
EXPOSE 8004
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8004"]
