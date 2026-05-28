FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r services/rag/requirements.txt
ENV PYTHONPATH=/app
EXPOSE 8004
CMD ["uvicorn", "services.rag.main:app", "--host", "0.0.0.0", "--port", "8004"]
